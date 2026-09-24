# General-Purpose Local Agent Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the local text-chat assistant reliably coordinate general browser tasks, request only necessary information, review meaningful choices and charges, execute authorized actions once, and report evidence-backed outcomes.

**Architecture:** Keep the existing browser adapter and Jev operation/target loop. Add structured task state, an event-driven GPT-4.1 mini coordinator, requirement/challenge handling, and commitment/evidence checks; integrate them into the existing agent and chat runner. Build and test these components before replacing the old coordination path, then verify them together through the real local chat and throwaway-browser fixtures.

**Tech Stack:** Python >=3.12, existing browser-harness==0.1.13 and httpx[http2]>=0.28,<1, standard-library dataclasses/decimal/threading, vanilla JavaScript, pytest, Ruff. No new runtime dependency or model provider.

**Spec:** [Approved local agent reliability design](../specs/2026-09-24-local-agent-reliability-design.md), all sections. The original design supplies unchanged browser/channel details only; conflicting historical plans are not execution instructions for this work.

**Status:** Ready for written-plan review and execution-method selection. Product implementation has not started. Baseline product commit: `22c4e45`; subsequent commits through `221c45c` change the design only.

## Global Constraints

- “Local reliability first. Server hosting, isolated customer sessions, and WhatsApp follow in separate work.”
- “General capability, with no supported-service whitelist or fixed list of supported tasks.”
- “Every user interaction stays in chat. No browser takeover, visiting another website, banking-app approval, device authentication, or instructions to click on the site.”
- “Text input for now.” CAPTCHA images, documents and uploads remain supported in chat.
- “GPT-4.1 mini coordinates the task; Jev selects browser operations and targets; application code enforces execution, information handling, and approval rules.”
- “No new website adapters, service-specific scripts, prepared navigation plans, or hardcoded field values.” Example providers/products in tests are fixtures, never runtime branches.
- One Jev operation/target request per step. Consume only its chosen operation's target head. Model output never becomes selectors, JavaScript, coordinates or file paths.
- “Never retry a browser mutation.” Record dispatch intent and result before post-action observation. Unknown outcomes cannot start another commitment.
- Dedicated headed Chrome only: `BU_NAME=jev`, `BU_CDP_URL=http://127.0.0.1:9333`, existing `~/.jev/chrome` profile. Never attach to everyday Chrome.
- “Keep the existing limit of three concurrent tasks and serialize tasks sharing an account/site.”
- One active chat question. Pause after 15 minutes and cancel after 24 hours, measured from display. Close resumably blocked task tabs after 24 hours without user activity.
- Budgets: 150 browser actions, 300 Jev decisions, 60 coordinator assessments; 3 consecutive non-wait stalls trigger recovery; at most 2 unsuccessful distinct recovery approaches per objective/state; 8 CAPTCHA rounds; 3 authentication rejections per flow; 2 CVV rejections; earlier website limits win.
- One schema-repair request for invalid model output. Outcome observation is bounded to 5 minutes; unresolved means unknown, never failed or successful by assumption.
- OTP lifetime at most 10 minutes, temporary payment-entry details at most 15 minutes, earlier website expiry wins. No new stored cards/CVVs; saved passwords require explicit consent and noninteractive access.
- Comparison is opt-in when no explicit provider/default exists. Explain separate logins and possible multiple OTPs before comparison login requests.
- Approval covers the actual action, complete material details, charges, destination and payment method. Offer selection is not purchase approval. `DONE` initiates verification; it never proves completion.
- Existing loopback server protections, upload/download bounds and safe text rendering remain. Do not add voice, a hosted browser, or automatic restart/resume.
- Credentials remain in ignored `.env`. Tests never call paid APIs; browser tests use a throwaway headless Chrome. Real model evaluation is separate and explicitly launched.
- Commit each completed plan task; never push. Preserve the pre-existing `.idea/jev-assistant.iml` modification. Use explicit paths when staging.

## Review Focus

1. A delayed secret reply arrives after another question opens: reject its stale identity without logging or classifying the value — Tasks 5 and 12.
2. Two tasks or providers share a hostname/account and a login redirects to another origin: serialize account access and release a secret only for the verified authentication flow — Tasks 6 and 12.
3. Indian-grouped prices, included tax, optional extras and unfamiliar fee labels change the total: preserve every observed charge and reconcile exact arithmetic — Task 7.
4. Page text claims to be instructions or tells the model to ignore the user's provider/budget: treat it as evidence, preserve the original goal and reject unauthorized state changes — Tasks 3 and 11.
5. The site says “finding a driver,” shows an old order, or downloads a file that never reaches chat: retain processing/unknown status or missing requirements rather than announce success — Tasks 9, 12 and 13.

## File and interface map

| File | Responsibility and scope |
|---|---|
| `assistant/state.py` (new) | Typed task container; validated facts, requirements, observations and evidence; revisions; progress accounting. No I/O. |
| `assistant/coordinator.py` (new) | GPT event selection, input/output schemas, semantic interpretation, question/update drafts, provider policy. No browser mutations or secret resolution. |
| `assistant/interaction.py` (new) | Requirement reuse, question lifecycle, challenge state, scoped temporary secrets, field-input cache. |
| `assistant/commit.py` (new) | Money/proposal checks, approval consumption, execution journal, outcome and completion verification. |
| `assistant/keychain.py` (new, small platform boundary) | Noninteractive Keychain get/set/delete for the existing `jev` service; no browser or model access. |
| `assistant/agent.py` | Wire the observe/coordinator/Jev/gates/execute/verify loop; remove direct-action shortcuts. Keep public constructor and command endpoints. |
| `assistant/browser.py`, `assistant/snapshot.js` | Observation completeness/context, code-owned visual targets, existing freshness/execution/event support. |
| `assistant/model.py`, `assistant/jobs.py`, `assistant/questions.py` | Reuse current clients; strict coordinator/field/output schemas; rewrite conflicting prompts; retain Jev head validation. |
| `assistant/profile.py` | Explicit defaults and password consent; remove card persistence and interactive vault calls; removable redaction entries. |
| `assistant/core.py` | Ordered inbound routing, deduplication, presentation queue, scheduling/resume, delivery receipts and tab lifetime. |
| `assistant/messages.py`, `assistant/channel_web.py`, `assistant/static/app.js`, `assistant/server.py` | Rich text within existing channel limits, explicit reply identities, masked ephemeral input, startup interruption reporting. |
| `tests/test_state.py`, `test_coordinator.py`, `test_interaction.py`, `test_commit.py`, `test_reliability.py`, `test_keychain.py` (new) | Pure regression and integration coverage; existing relevant test files are updated alongside each integration. |
| `assistant/fixtures/reliability.html` (new) | One generic fixture with controllable authentication, choices, fees, challenges, outcomes and delays; no production site logic. |
| `docs/reliability-evaluation.md` (new), `docs/smoke.md`, `docs/known-issues.md`, `README.md`, `AGENTS.md` | Reproducible validation, declared live cases, honest results and current rules. |

All paths below are repository-relative. New public interfaces are defined in their owning task. JSON record shapes are explicit below; use ordinary dictionaries at model/browser boundaries and a dataclass for task ownership, avoiding a new schema framework. Standard-library types in signatures carry their ordinary meaning. Keep all temporary raw secret values outside `TaskState` and model payloads.

Tasks 1–10 build independently tested components. Task 11 switches the agent to those components; Task 12 switches chat orchestration. Do not leave two competing runtime policies or permanent migration flags. Keep existing public call signatures until their integration task; add new component entry points without prematurely removing the old caller's contract. Existing tests that encode superseded behavior change when their runtime path changes, with replacement behavioral assertions rather than deletion of coverage. Intermediate component commits are not a release and must not be used for real customer transactions before integration checks pass.

---

### Task 1: Structured task state, facts and evidence

**Files:** Create `assistant/state.py`, `tests/test_state.py`, `tests/reliability_fakes.py`; read `assistant/agent.py:91`, `assistant/core.py:37`, `tests/fakes.py`.

**Interfaces:**
- `TaskState(task_id: str, goal: str)` owns `revision`, `status`, `objective`, `updates`, `constraints`, `facts`, `requirements`, `questions`, `challenges`, `observations`, `evidence`, `executions`, `progress`, `approval`, `proposal`, `completion`, `delivered`, `diagnostics`.
- `observe_state(state: TaskState, page: dict) -> str` stores a sanitized observation under a code-owned ID.
- `cite(state: TaskState, observation_id: str, quote: str, *, kind: str = "page") -> str` validates provenance and creates an evidence ID.
- `put_fact(state: TaskState, key: str, value: object, *, source: str, scope: str = "task", depends_on: tuple[str, ...] = (), evidence: tuple[str, ...] = ()) -> None`.
- `amend(state: TaskState, text: str, changed_keys: set[str]) -> None`; `public_context(state: TaskState) -> dict`.

- [ ] **Step 1: Write revision and provenance tests.** In `tests/test_state.py`:

```python
import pytest
from assistant.state import TaskState, amend, cite, observe_state, put_fact, public_context

def test_address_change_preserves_phone_and_invalidates_dependent_offer():
    s = TaskState("t1", "Get groceries")
    put_fact(s, "phone", "[profile:phone]", source="user")
    put_fact(s, "address", "Home", source="user")
    put_fact(s, "offer", "o1", source="user", depends_on=("address",))
    s.approval = {"revision": 0}
    amend(s, "Deliver to Office", {"address"})
    assert s.facts["phone"]["valid"]
    assert not s.facts["offer"]["valid"]
    assert s.approval is None and s.revision == 1
    assert public_context(s)["goal"] == "Get groceries"

def test_evidence_cannot_be_invented_or_taken_from_another_observation():
    s = TaskState("t1", "Find a fare")
    oid = observe_state(s, {"url": "https://cab.test/", "text": "Fare estimate ₹200", "actions": []})
    assert cite(s, oid, "Fare estimate ₹200") in s.evidence
    with pytest.raises(ValueError):
        cite(s, oid, "Driver assigned")
```

- [ ] **Step 2: Run the red test.** `uv run pytest tests/test_state.py -q`; expect an import failure for the new state module, not a paid API or browser call.
- [ ] **Step 3: Implement the container and record validation.** Use `@dataclass` and `field(default_factory=...)`; initialize status `running`, revision `0`, objective to the goal. Every fact stores `{key, value, source, scope, revision, depends_on, evidence, valid}`. Each observation stores `{id, revision, url, origin, text, actions, completeness}`; evidence stores `{id, observation_id, revision, origin, kind, quote}`. Allocate IDs in code. A page-derived fact requires matching evidence; user facts take precedence over page inference. Amendments invalidate transitive dependencies and approvals, never unrelated answers. Core invalidation logic:

```python
from dataclasses import dataclass, field

@dataclass
class TaskState:
    task_id: str
    goal: str
    revision: int = 0
    status: str = "running"
    objective: str = ""
    updates: list = field(default_factory=list)
    constraints: dict = field(default_factory=dict)
    facts: dict = field(default_factory=dict)
    requirements: dict = field(default_factory=dict)
    questions: dict = field(default_factory=dict)
    challenges: dict = field(default_factory=dict)
    observations: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    executions: dict = field(default_factory=dict)
    progress: dict = field(default_factory=dict)
    approval: dict | None = None
    proposal: dict | None = None
    completion: list = field(default_factory=list)
    delivered: dict = field(default_factory=dict)
    diagnostics: list = field(default_factory=list)

    def __post_init__(self):
        self.objective = self.objective or self.goal
```

All writes go through the validated module functions or code-owned lifecycle methods; coordinator dictionaries cannot directly replace this object. Goal-derived evidence may include page, browser-event, visual-observation or artifact provenance. A visual annotation is tied to a code-owned screenshot reference, not validated by pretending its text was in the DOM. Raw screenshots and browser objects do not enter `public_context`.

```python
affected = set(changed_keys)
while True:
    more = {key for key, fact in state.facts.items()
            if affected.intersection(fact["depends_on"])} - affected
    if not more:
        break
    affected.update(more)
for key in affected:
    if key in state.facts:
        state.facts[key]["valid"] = False
state.revision += 1
state.updates.append(text)
state.approval = None
```

Validate record enums and transition inputs, never accept coordinator-written execution results or approvals. Completion requirements carry `{id, description, kind, material_keys, evidence_ids, execution_id, artifact_id}`; allowed kinds are page/state/action/artifact, not a task-name list.
- [ ] **Step 4: Add reusable fixture builders.** `tests/reliability_fakes.py`:

```python
from assistant.state import TaskState, cite, observe_state
from fakes import fake_page

def observed(text="Checkout", goal="Complete the requested task", url="https://shop.test/"):
    s = TaskState("t1", goal)
    page = fake_page(text=text, url=url)
    oid = observe_state(s, page)
    return s, page, oid

def evidence_for(s, oid, quote):
    return (cite(s, oid, quote),)
```

- [ ] **Step 5: Add tests for bad status transitions, conflicting page/user facts, transitive invalidation, immutable original goal, and stale observation references.** Use parameterized values including empty quote, nonexistent observation, different task and a previous revision. Verify all diagnostics/context serialize without browser objects or raw-secret fields.
- [ ] **Step 6: Verify and commit.** `uv run pytest tests/test_state.py -q` and `uv run ruff check assistant/state.py tests/test_state.py tests/reliability_fakes.py`; then stage exactly those files and commit `feat: add structured task state and evidence`.

### Task 2: Complete observations and code-owned visual targets

**Files:** Modify `assistant/snapshot.js`, `assistant/browser.py:580`, `assistant/browser.py:618`, `assistant/browser.py:730`, `assistant/browser.py:1040`, `assistant/model.py:90`; extend `tests/test_browser.py`, `tests/test_frames.py`, `tests/test_look.py`; create `assistant/fixtures/reliability.html`.

**Interfaces:**
- Preserve `Browser.observe(screenshot=False) -> dict`, `fresh(page, action=None) -> bool`, and `act(operation, action, page, **inputs) -> dict`.
- Observation adds `completeness={text_truncated: bool, omitted_actions: int, frames_incomplete: list[str]}` and per-element `context={form: str, section: str, placeholder: str, field_role: str}`.
- `Browser.visual_candidates(page: dict, annotations: list[dict]) -> list[dict]` maps only existing screenshot/grid refs to candidates; `annotations` contain `{cell, label, text}` and no coordinates. Each candidate has an observation/screenshot identity and code-owned geometry.

- [ ] **Step 1: Add browser fixture and failing coverage.** Build a form with 260 controls, two inputs both labeled “Code” under distinct login/payment headings, a changing countdown, and a cross-origin frame. Add:

```python
import pytest

@pytest.mark.browser
def test_truncated_observation_reports_omissions(browser_at):
    b = browser_at("/reliability.html?case=large")
    page = b.observe()
    assert page["completeness"]["omitted_actions"] > 0
    assert page["completeness"]["text_truncated"] is True
```

Also assert the two Code controls retain their different form contexts. Fixture case `large` must render >6,000 visible text characters and >250 in-viewport candidate controls so the test genuinely crosses both limits.
- [ ] **Step 2: Run** `uv run pytest -m browser tests/test_browser.py tests/test_frames.py -q`; expect missing observation metadata assertions to fail.
- [ ] **Step 3: Add context and completeness without weakening freshness.** Record truncation before slicing and aggregate frame omissions in `merge`. Keep node IDs, frame/session identity and geometry validation in code. Snapshot output shape:

```javascript
const completeness = {
  text_truncated: fullText.length > textLimit,
  omitted_actions: Math.max(0, candidates.length - actionLimit),
  frames_incomplete: []
};
```

Define `fullText`, `textLimit=6000`, `candidates`, and `actionLimit=250` from the existing snapshot collection before clipping. Do not treat missing controls on incomplete pages as absent. Expose scroll/read-more opportunities through existing operations; do not remove bounds to send unlimited page content to models.
- [ ] **Step 4: Change visual interpretation into annotation.** Preserve screenshot masking and code-generated grids; store the grid with its screenshot identity. GPT returns annotations for supplied cells, then Jev selects the indexed candidate. `LOOK` observes/refines only; it no longer performs GPT's chosen click. Freshness failure invalidates candidates. DRAG destinations follow the same rule. Browser targets resolve their point internally; the model never submits a point.
- [ ] **Step 5: Add unit tests for annotation with unknown cell, invented coordinates, stale screenshot and incomplete frame; add browser tests for stale visual target and occlusion.** Existing `jobs.look`/agent wiring changes in Tasks 3 and 11; this task introduces the candidate representation without enabling the old shortcut on the new path.
- [ ] **Step 6: Verify and commit.** Run changed unit tests, `uv run pytest -m browser tests/test_browser.py tests/test_frames.py tests/test_look.py -q`, `node --check assistant/snapshot.js`, and Ruff on changed Python files. Commit `feat: expose observation context and grounded visual targets`.

### Task 3: Event-driven GPT coordinator and strict schemas

**Files:** Create `assistant/coordinator.py`, `tests/test_coordinator.py`; modify `assistant/jobs.py`, `assistant/model.py:217`, `assistant/questions.py`; extend `tests/test_jobs.py`, `tests/test_model.py`.

**Interfaces:**
- `events_for(state: TaskState, page: dict, *, event: str | None = None) -> tuple[str, ...]`.
- `assess(state: TaskState, page: dict, event: str, *, client=model.text_json) -> dict` calls GPT and validates one assessment, with at most one schema repair.
- `apply_assessment(state: TaskState, assessment: dict) -> None` applies only permitted, validated semantic changes.
- `interpret_message(text: str, tasks: list[dict], chat: list[dict], *, client=model.text_json) -> dict` returns `{intent, task_id, ambiguous, reply}`; intent is ANSWER/AMEND/NEW_TASK/CANCEL/STATUS/OTHER/RESUME. Task IDs must be supplied candidates.
- `annotate_visual(state: TaskState, page: dict, image: str, cells: dict, *, client=model.text_json) -> list[dict]` returns Task 2 annotations only; never use it to solve a CAPTCHA.

- [ ] **Step 1: Write strict output/repair tests.** Define the valid wire shape here and use it consistently:

```python
import pytest
from assistant.coordinator import assess
from reliability_fakes import observed

def assessment(**changes):
    return {"objective": "Inspect the current page", "facts": [], "requirements": [],
            "completion": [], "auth": None, "progress": None, "proposal": None,
            "outcome": None, "notice": None, "recovery": None, **changes}

def test_coordinator_cannot_emit_a_browser_operation_or_approval():
    s, page, _ = observed()
    calls = []
    def client(system, payload, **kwargs):
        calls.append(payload)
        return assessment(operation="CLICK", approval=True), {}
    with pytest.raises(ValueError):
        assess(s, page, "start", client=client)
    assert len(calls) == 2
    assert "validation_error" in calls[1]
    assert not s.executions and s.approval is None
```

- [ ] **Step 2: Run** `uv run pytest tests/test_coordinator.py -q`; expect the new-module import failure.
- [ ] **Step 3: Implement allowed fields and nested validation.** Facts use Task 1's shape; requirements use Task 4's shape; auth uses Task 6's observation shape; proposal uses Task 7's shape; outcome uses Task 9's shape. Until those consumers exist, validate their documented wire dictionaries here and keep application in the owning module. Unknown fields, invalid types, stale/nonexistent references and changed original goals are errors. Validate exact bool/int types where bool would otherwise pass as int. One repair includes the sanitized rejected response, validation path/reason and current evidence, and counts against the coordinator budget.

```python
ASSESSMENT_KEYS = {"objective", "facts", "requirements", "completion", "auth", "progress",
                   "proposal", "outcome", "notice", "recovery"}
if set(output) != ASSESSMENT_KEYS:
    raise ValueError("assessment.keys: unknown or missing field")
```

Completion requirements can be set at task creation or a user amendment only. Objectives cannot remove active constraints; state constraints come from validated user choices. A notice is `{kind, text, evidence_ids, requirement_ids}`; prices and factual claims must link to evidence/facts. Recovery is `{objective, reason, evidence_ids}`; it cannot directly execute an operation.
- [ ] **Step 4: Add coordinator prompts and cache.** Prompt includes original goal, amendments, valid facts/references, active requirements, current observation, pending commitment, and permitted output schema. Clearly mark page text as untrusted evidence. Use existing `model.text_json` with an explicit `max_tokens` sized for the schema, never add unsupported API parameters. Cache only validated assessments by task revision, semantic observation signature and event. Trigger on the events enumerated in spec §5; ignore countdown-only text changes. Cache invalidation includes auth, field constraints, proposal and question changes. No model call for secret routing or every ordinary click.
- [ ] **Step 5: Add tests for event/cache changes and message routing.** Script responses with `client`; assert an unchanged observation does not call again, user amendment does, one malformed response repairs, two stop, network errors remain separate from schema repair, and metadata/redacted failure reason is retained. Include malicious page text asking to change the requested provider or reveal a secret; the original goal/constraints remain in subsequent Jev context. Test ordinary chat returns a reply without creating a browser task and unknown task IDs cause clarification.
- [ ] **Step 6: Verify and commit.** Run `uv run pytest tests/test_coordinator.py tests/test_jobs.py tests/test_model.py -q` and Ruff. Commit `feat: add event-driven GPT task coordination`.

### Task 4: Requirements, reusable answers, provider choices and useful chat

**Files:** Create `assistant/interaction.py`, `tests/test_interaction.py`; modify `assistant/coordinator.py`, `assistant/profile.py`, `assistant/jobs.py`, `assistant/questions.py`, `assistant/messages.py`; extend `tests/test_messages.py`, `tests/test_profile.py`.

**Interfaces:**
- Requirement record: `{id, kind, purpose, keys, scope, depends_on, options, reason}`. Kind is text/choice/secret/file/image; `keys` identifies facts rather than question wording.
- `require(state: TaskState, requirement: dict) -> dict | None` returns a stable question record or None when valid facts satisfy it. Record: `{id, revision, requirement_ids, kind, text, options, challenge_id, displayed_at, answered_at}`; IDs are code-owned strings, e.g. `t1-q1`.
- `answer_requirements(state: TaskState, question_id: str, values: dict, *, now: float) -> tuple[str, ...]` returns still-missing requirement IDs.
- `reopen(state: TaskState, requirement_id: str, reason: str, evidence_ids: tuple[str, ...]) -> None` accepts only the spec's reask reasons.
- `provider_mode(state: TaskState, defaults: dict) -> dict` returns `{mode: "selected"|"compare"|"ask", provider: str|None, notice: str|None}`.
- `chat_update(state: TaskState, notice: dict) -> dict | None` validates/deduplicates and returns existing channel message shape.
- `Profile.set_default(key: str, value: object, *, consent: bool) -> None` stores explicit structured defaults with source; old free-form preferences remain suggestions unless unambiguously user-authorized defaults.

- [ ] **Step 1: Write the repeated-question regression.**

```python
from assistant.interaction import require, answer_requirements
from assistant.state import TaskState

def test_rewording_does_not_reask_for_a_valid_phone():
    s = TaskState("t1", "Continue my task")
    requirement = {"id": "phone", "kind": "text", "purpose": "login", "keys": ["phone"],
                   "scope": "https://shop.test", "depends_on": [], "options": [], "reason": "missing"}
    q = require(s, requirement)
    assert q is not None
    answer_requirements(s, q["id"], {"phone": "[profile:phone]"}, now=1)
    assert require(s, {**requirement, "reason": "new wording"}) is None
```

- [ ] **Step 2: Run** `uv run pytest tests/test_interaction.py -q`; expect missing-interface failures.
- [ ] **Step 3: Implement identity and answer reuse.** Match requirements by task, purpose, fact keys and scope, never text similarity. Related non-secret requirements may share a question; keep each answer's provenance and unresolved keys separately. A selected option stores its semantic details and evidence, not its transient element ID. Compare materially different choices; a shared service word is insufficient. Answers only add facts; they never invoke `Browser.act`.

```python
def requirement_key(requirement):
    return (requirement["purpose"], requirement["scope"], tuple(sorted(requirement["keys"])))
```

- [ ] **Step 4: Implement provider policy and explicit defaults.** Order: current explicit provider/comparison request, applicable explicit saved default, otherwise preferred-provider-or-compare question. Previous provider/address history is a suggestion only. Comparison notice mentions possible separate logins/multiple OTPs; an already requested comparison does not need another opt-in. Accept free-text providers; candidate identity/availability must be observed before credentials are released. Unknown location becomes a requirement, never Mac geolocation. Skipped providers remain unavailable in the result; provider selection never creates a default implicitly.
- [ ] **Step 5: Add evidence-backed updates and rich rendering.** Start with understood goal/defaults, then useful findings, choices, actual delays/login requirements, final review and verified result. Deduplicate notices using task revision, kind and their semantic evidence/requirement IDs. Preserve product/ride differences in body text even when button labels must be short. `messages.render` must split long reviews into text messages and place the actionable question last, preserving its question ID. No truncation of fees to the old eight-line card limit.

```python
def test_long_review_keeps_every_charge_before_confirmation():
    from assistant.messages import render
    lines = [f"Observed charge {n}: ₹1" for n in range(30)]
    message = {"kind": "confirm", "text": "\n".join(lines),
               "options": [{"id": "yes", "label": "Confirm"}, {"id": "no", "label": "Change"}]}
    payloads = render(message, question_id="t1-q1")
    bodies = [p["text"]["body"] if p["type"] == "text" else p["interactive"]["body"]["text"]
              for p in payloads if p["type"] in {"text", "interactive"}]
    assert all(line in "\n".join(bodies) for line in lines)
```

- [ ] **Step 6: Test combined questions/partial answers, unknown-provider free text, explicit comparison, no default from past order, skip-provider results, valid-default announcement, duplicated notices and stale option evidence.** Assert no extra question after already supplied facts. Verify all rendered text uses existing plain-text DOM behavior, never HTML.
- [ ] **Step 7: Verify and commit.** `uv run pytest tests/test_interaction.py tests/test_messages.py tests/test_profile.py tests/test_coordinator.py -q`; Ruff. Commit `feat: reuse answers and clarify provider choices in chat`.

### Task 5: Scoped temporary secrets and noninteractive password storage

**Files:** Modify `assistant/interaction.py`, `assistant/profile.py`, `assistant/model.py:254`, `assistant/questions.py`; create `assistant/keychain.py`, `tests/test_keychain.py`; extend `tests/test_interaction.py`, `tests/test_profile.py`, `tests/test_model.py`, `tests/fakes.py`.

**Interfaces:**
- `SecretStore(clock: Callable[[], float])` with `put(value: str, *, task_id: str, question_id: str, challenge_id: str, origin: str, purpose: str, field_role: str, expires_at: float) -> str`, `resolve(ref: str, **same_scope) -> str`, `consume(ref: str) -> None`, `purge(task_id: str | None = None) -> None`.
- Scope includes exactly the six keys above; expired, submitted/consumed or mismatched references raise `ValueError` without including the value.
- `field_cache_key(state: TaskState, helper_input: dict, challenge_id: str | None) -> str` hashes the complete sanitized helper payload, revision and challenge, not just host/label.
- `Secrets.remove(placeholder: str) -> None` removes redaction copies only after retained task/model/error records are sanitized.
- `keychain.password(operation: str, key: str, value: str | None = None) -> str | None`; operations get/set/delete; failures raise `KeyError` or `RuntimeError` with non-secret messages. `Vault` wraps this, validates allowed namespaces and never falls back to interactive CLI calls.
- `Profile.legacy_card_keys() -> list[str]`; `Vault.delete_legacy_cards(keys: list[str], *, consent: bool) -> None` deletes only enumerated existing `jev` card entries, only following explicit chat consent.

- [ ] **Step 1: Write cross-provider and consumed-secret tests.**

```python
import pytest
from assistant.interaction import SecretStore

def test_login_code_never_serves_another_provider_or_payment():
    store = SecretStore(clock=lambda: 10)
    scope = dict(task_id="t1", question_id="t1-q1", challenge_id="c1", origin="https://a.test",
                 purpose="login", field_role="otp")
    ref = store.put("123456", **scope, expires_at=100)
    assert store.resolve(ref, **scope) == "123456"
    for change in ({"origin": "https://b.test"}, {"purpose": "payment"}, {"challenge_id": "c2"}):
        with pytest.raises(ValueError):
            store.resolve(ref, **{**scope, **change})
    store.consume(ref)
    with pytest.raises(ValueError):
        store.resolve(ref, **scope)
```

- [ ] **Step 2: Run** `uv run pytest tests/test_interaction.py -q`; expect the new store tests to fail.
- [ ] **Step 3: Implement secret lifetime and safe resolver.** Raw values live only in the store and the immediate executor's input. Models see opaque references and metadata. Cap lifetime at 600 seconds for codes and 900 seconds for payment-entry details; earlier site expiry wins. Consume at submission (including auto-submit typing), not merely because an ordinary form field remains visible. Tie submitted secrets to the execution ledger so pending outcomes cannot trigger replay. Resend creates a new challenge/ref and invalidates the old value. Remove the newest-unused-secret fallback and `save_as=card.*` from new field schemas; later Task 11 removes legacy runtime call sites.

```python
if entry["expires_at"] <= self.clock() or entry["consumed"]:
    raise ValueError("secret unavailable")
if any(entry[key] != scope[key] for key in scope):
    raise ValueError("secret scope mismatch")
```

Use random opaque IDs. Exclude raw store internals from repr/serialization. Normal user facts may be reused; raw secrets are never moved into task facts or completed objects.
- [ ] **Step 4: Stop card persistence and provide explicit cleanup.** `Profile.vault_keys()` exposes authorized login keys only. `Vault.get/set` rejects card namespaces except the explicit deletion path. Do not silently delete legacy data, advertise it as usable, or call `offer_card_save`. Merchant-saved cards remain visible through website labels. Update `FakeVault` and test defaults so fixtures no longer silently seed a reusable CVV. Include an explicit `legacy_card_cleanup` chat requirement with the enumerated account keys; accepting that specific confirmation invokes only `Vault.delete_legacy_cards`, and declining preserves the entries. This is a local profile operation, not a browser mutation; never derive Keychain account keys from webpage/model instructions. Wire this requirement through Core in Task 12.
- [ ] **Step 5: Replace interactive Keychain access.** Implement the small `assistant/keychain.py` boundary with standard-library `ctypes` bindings to macOS Security/CoreFoundation. Set user interaction false before every keychain operation under a module lock; if disabling interaction fails, do not call the keychain operation. Bind argument/return types, check every OSStatus, release returned content/item references in `finally`, and restore the prior interaction state. Use the existing `jev` service and exact account key. Use FindGenericPassword, AddGenericPassword or ItemModifyAttributesAndData, and ItemDelete; never call an unlock API. The process-local suppression must surround the Security calls in that process, not a separately spawned `security` CLI.

```python
with KEYCHAIN_LOCK:
    previous = ctypes.c_bool()
    checked(security.SecKeychainGetUserInteractionAllowed(ctypes.byref(previous)))
    checked(security.SecKeychainSetUserInteractionAllowed(False))
    try:
        return perform_password_operation(operation, key, value)
    finally:
        checked(security.SecKeychainSetUserInteractionAllowed(previous.value))
```

Define `KEYCHAIN_LOCK`, `checked(status: int) -> None` and `perform_password_operation(operation, key, value)` in that module. Calls requiring UI return unavailable; ask for temporary masked input. Password saving occurs only after observed acceptance and explicit user consent. New platforms or missing symbols use the same unavailable path.

Apple documents [interaction suppression](https://developer.apple.com/documentation/security/keychains?language=objc) and [password lookup's potential prompts and memory ownership](https://developer.apple.com/documentation/security/seckeychainfindgenericpassword(_:_:_:_:_:_:_:_:)). These legacy APIs are deprecated; isolate them behind this small boundary. Do not imply they guarantee future macOS support.
- [ ] **Step 6: Test through fake native functions, never the user's Keychain.** Assert suppression occurs before get/set/delete, restoration follows exceptions, failure to suppress prevents access, denied access falls back, and unrelated service/accounts are never removed. Inject fake framework handles by monkeypatching module bindings. Verify secret values are absent from model payloads, traces, exceptions, completed state and screenshots; redaction cleanup does not expose older entries. Include CVV, card number, password, OTP and Unicode secret strings.
- [ ] **Step 7: Verify and commit.** `uv run pytest tests/test_interaction.py tests/test_profile.py tests/test_keychain.py tests/test_model.py -q`; Ruff. Commit `fix: scope temporary secrets and remove card vault reuse`.

### Task 6: Authentication and human-solved CAPTCHA transitions

**Files:** Modify `assistant/interaction.py`, `assistant/coordinator.py`, `assistant/jobs.py`, `assistant/questions.py`; extend `tests/test_interaction.py`, `tests/test_login.py`, `tests/test_wrong_data.py`, `tests/test_help.py`; extend `assistant/fixtures/reliability.html`.

**Interfaces:**
- `observe_auth(state: TaskState, event: dict) -> dict` validates an event and updates a flow/challenge. Event shape: `{flow_id, challenge_id, status, origin, purpose, field_role, evidence_ids, execution_id, expires_at, allowed_origins}`. Status is unknown/required/challenge/submitted/authenticated/rejected/expired/unsupported.
- `captcha_answer(challenge: dict, reply: str, *, revision: int) -> dict`; challenge shape includes `{id, kind, revision, tiles, rounds, observation_id}`; kind text/tiles/unsupported.

- [ ] **Step 1: Write authentication and numeric-CAPTCHA regressions.**

```python
from assistant.interaction import captcha_answer, observe_auth
from reliability_fakes import observed, evidence_for

def test_persistent_sign_in_link_does_not_override_authenticated_evidence():
    s, page, oid = observed("Sign in | Your account | Saved addresses: Home")
    proof = evidence_for(s, oid, "Your account | Saved addresses: Home")
    event = dict(flow_id="f1", challenge_id=None, status="authenticated", origin="https://shop.test",
                 purpose="login", field_role="otp", evidence_ids=proof, execution_id=None,
                 expires_at=None, allowed_origins=["https://shop.test"])
    assert observe_auth(s, event)["status"] == "authenticated"

def test_numeric_text_captcha_is_never_a_tile_list():
    challenge = dict(id="c1", kind="text", revision=1, tiles=[], rounds=0, observation_id="o1")
    assert captcha_answer(challenge, "2468", revision=1) == {"kind": "text", "value": "2468"}
```

- [ ] **Step 2: Run** `uv run pytest tests/test_interaction.py -q`; expect absent-interface failures.
- [ ] **Step 3: Implement explicit flow transitions.** Login is needed only when the objective requires account access or observed access is blocked. Existing authenticated evidence can establish a reused session without entering credentials. Rejection requires a relevant submission execution and subsequent observed error; reselecting or retyping a field is not rejection. Rejection counts live on the flow and survive challenge resend. Distinguish login, payment and CVV purposes. Verify auth redirects from observed navigation/flow evidence before adding permitted origins; never trust a model-supplied origin list alone. Website flow evidence can refine a scope but cannot turn a login code into a payment code.

```python
if event["status"] == "rejected":
    execution = state.executions.get(event["execution_id"])
    if not execution or execution["challenge_id"] != event["challenge_id"]:
        raise ValueError("rejection has no matching submission")
    if not event["evidence_ids"]:
        raise ValueError("rejection requires observed evidence")
```

- [ ] **Step 4: Implement CAPTCHA response validation.** Text answers preserve digits and leading zeroes. Tile answers accept code-owned labels from the current revision only and become facts for Jev's subsequent selections; they never directly invoke a click. If the challenge changes, discard stale selection refs and send the fresh image. Track eight rounds; unsupported gestures become a clear chat blocker. Do not call an image model to solve the challenge.
- [ ] **Step 5: Test expired codes, resend counts, slow auto-submit, unsupported passkeys/app approval, allowed observed redirects versus arbitrary origins, numeric formatting errors and two fields called Code with different purposes.** Add text/tile fixture cases. A public search with a sign-in link proceeds without login. Chat-only alternatives are considered before reporting unsupported; instructions to operate the website/device are rejected. Existing login tests that require sign-in on every page are replaced during Task 11 wiring.
- [ ] **Step 6: Verify and commit.** Run changed pure tests and relevant fixture browser tests, with fake model responses. Commit `fix: verify authentication and scope human challenge answers`.

### Task 7: Complete cart review, exact money and promotion advice

**Files:** Create `assistant/commit.py`, `tests/test_commit.py`; modify `assistant/coordinator.py`, `assistant/jobs.py`, `assistant/questions.py`; extend `tests/test_shop.py`, `assistant/fixtures/reliability.html`.

**Interfaces:**
- `money_minor(text: str, *, currency: str, exponent: int) -> int` parses an observed amount under an explicit currency/exponent, rejects ambiguous grouping or unsupported representation, never uses float.
- `review_total(lines: list[dict], total_minor: int, *, currency: str) -> dict` returns `{valid, computed_minor, reasons}`.
- Monetary line: `{id, label, amount_minor, currency, role, included_in, evidence_ids}`. Role item/subtotal/discount/fee/tax/tip/rounding/later_estimate; `included_in` is another line ID or None. Preserve unknown fee labels.
- Proposal: `{task_id, revision, action, service, account, destination, items, option, timing, payment, currency, total_minor, total_kind, later_terms, lines, evidence_ids}`. `total_kind` exact/estimate/not_applicable. Nonmonetary actions have null money fields. Line extraction references observed text; code validates values/arithmetic.
- `promotion_advice(base: dict, alternative: dict, terms: dict) -> dict`. Both baskets contain verified `{total_minor, delivery_minor, qualifying_subtotal_minor, currency, evidence_ids}`. Terms contain `{operator, threshold_minor, eligible, conditions_verified, evidence_ids}`. Return `{eligible, extra_spend_minor, delivery_saving_minor, verified}`.

- [ ] **Step 1: Add arithmetic and recommendation regressions.**

```python
from assistant.commit import money_minor, review_total, promotion_advice

def test_indian_grouping_and_included_tax_are_not_double_counted():
    assert money_minor("₹1,23,456.78", currency="INR", exponent=2) == 12345678
    lines = [dict(id="s", label="Subtotal", amount_minor=10000, currency="INR", role="subtotal",
                  included_in=None, evidence_ids=["e1"]),
             dict(id="t", label="Tax included", amount_minor=500, currency="INR", role="tax",
                  included_in="s", evidence_ids=["e2"]),
             dict(id="f", label="Rain fee", amount_minor=1000, currency="INR", role="fee",
                  included_in=None, evidence_ids=["e3"])]
    assert review_total(lines, 11000, currency="INR")["valid"]
    assert not review_total(lines, 10000, currency="INR")["valid"]

def test_free_delivery_can_increase_total_spend():
    base = dict(total_minor=19000, delivery_minor=3500, qualifying_subtotal_minor=14500,
                currency="INR", evidence_ids=["e1"])
    alternative = dict(total_minor=30000, delivery_minor=0, qualifying_subtotal_minor=29000,
                       currency="INR", evidence_ids=["e2"])
    terms = dict(operator="gt", threshold_minor=19900, eligible=True,
                 conditions_verified=True, evidence_ids=["e3"])
    result = promotion_advice(base, alternative, terms)
    assert result["delivery_saving_minor"] == 3500
    assert result["extra_spend_minor"] == 11000
```

- [ ] **Step 2: Run** `uv run pytest tests/test_commit.py -q`; expect missing-module failure.
- [ ] **Step 3: Implement exact arithmetic and breakdown validation.** Parse using `Decimal` after validating separators and currency; reject NaN/Infinity, bool amounts, unknown exponent, fractional minor units and mixed currency. Do not silently round. Model extraction includes the original amount text; verify its conversion against the observation. Sum only payable root lines, avoiding item/subtotal and included-tax double counting; reject cycles/unknown `included_in` refs, missing coverage or an unexplained mismatch. Estimated later charges remain outside today's payable total and are disclosed. Pure monetary routines validate structure/arithmetic; proposal validation additionally validates every evidence reference.

```python
payable = [line for line in lines
           if line["included_in"] is None and line["role"] != "later_estimate"]
computed = sum(line["amount_minor"] for line in payable)
```

- [ ] **Step 4: Implement verified promotion comparisons.** Apply the observed `gt` versus `gte` threshold, qualifying subtotal and all relevant conditions. `95 * 2 < 199` must not qualify. When terms, total or membership eligibility are unknown, `verified=False`; never promise free delivery. Show both additional spend and fee reduction. No automatic extra item, substitution, membership or changed payment method. Code produces the amounts; GPT explains them from verified data.
- [ ] **Step 5: Require cart inspection for a proposal.** Coordinator objective requests visible cart/booking breakdown including expandable sections; Jev opens them. Preserve all items and unfamiliar charges. Existing unrelated cart items and optional tips/donations/memberships become material choices. Missing/unread fees are unknown, not zero. Revalidate changes and do not cut reviews to eight lines. Review includes destination, timing, payment and total; existing saved-card labels stay masked.
- [ ] **Step 6: Add parameterized tests.** Cover western/Indian grouping, decimal separator ambiguity, negative discounts, explicit rounding, fee cycles, strict/inclusive threshold at exactly 199, excluded items, expired offers, saved-card-dependent discount, membership condition, quantity mismatch and unchanged total with changed line-item terms. Browser fixture reveals an extra fee only after expanding its breakdown; the approval must include it.
- [ ] **Step 7: Verify and commit.** `uv run pytest tests/test_commit.py tests/test_shop.py tests/test_jobs.py -q`; changed browser fixtures and Ruff. Commit `feat: verify cart charges and explain promotion tradeoffs`.

### Task 8: Exact proposal approval and durable execution records

**Files:** Modify `assistant/commit.py`, `assistant/rules.py`, `assistant/browser.py:662`; extend `tests/test_commit.py`, `tests/test_gate.py`, `tests/test_browser_unit.py`, `tests/test_traces.py`.

**Interfaces:**
- `proposal_key(proposal: dict) -> str` canonically hashes all material fields, excluding observation/ephemeral evidence IDs and display-only text.
- `approve(state: TaskState, proposal: dict, *, question_id: str, response: str) -> dict` requires the current confirmation question's affirmative reply and records `{id, proposal_key, revision, question_id, consumed}`.
- `can_commit(state: TaskState, current: dict) -> bool`; `consume_approval(state: TaskState, current: dict) -> str` validates and consumes once, returning approval ID.
- `Journal(path: Path, redact: Callable[[object], object])` has `append(record: dict) -> None`, `records() -> list[dict]`, `interrupted() -> list[dict]`.
- `dispatch_once(state: TaskState, journal: Journal, decision: dict, page: dict, *, act: Callable[[], dict], is_fresh: Callable[[], bool], interrupted: Callable[[], bool], proposal: dict | None = None, challenge_id: str | None = None) -> dict` records one execution and calls `act` at most once. It never observes the browser itself.

- [ ] **Step 1: Write the stale-price and changed-destination regression.**

```python
from copy import deepcopy
from assistant.commit import proposal_key

def test_item_price_remaining_on_page_cannot_keep_an_old_total_approved():
    proposal = dict(task_id="t1", revision=0, action="purchase", service="shop.test", account="a1",
                    destination="Home", items=[{"name": "Drink", "quantity": 1}], option=None,
                    timing="now", payment="cash", currency="INR", total_minor=7900,
                    total_kind="exact", later_terms=[], lines=[], evidence_ids=[])
    changed = deepcopy(proposal)
    changed["total_minor"] = 9500
    assert proposal_key(proposal) != proposal_key(changed)
    changed = {**proposal, "destination": "Office"}
    assert proposal_key(proposal) != proposal_key(changed)
    assert proposal_key(proposal) == proposal_key({**proposal, "evidence_ids": ["fresh-observation"]})
```

This pure hashing test does not make a proposal approvable. Approval also requires the evidence and totals validation established in Task 7 and a live confirmation question.
- [ ] **Step 2: Add an execution-order test.**

```python
import pytest
from assistant.browser import ExecutionUncertain
from assistant.commit import Journal, dispatch_once
from reliability_fakes import observed

def test_uncertain_dispatch_is_recorded_and_never_replayed(tmp_path):
    s, page, _ = observed()
    journal = Journal(tmp_path / "task.jsonl", redact=lambda x: x)
    calls = []
    def act():
        calls.append(1)
        assert journal.records()[-1]["phase"] == "dispatching"
        raise ExecutionUncertain("transport disconnected")
    decision = {"id": "d1", "operation": "CLICK", "choice": "e1"}
    result = dispatch_once(s, journal, decision, page, act=act,
                           is_fresh=lambda: True, interrupted=lambda: False)
    assert result["status"] == "unknown"
    with pytest.raises(ValueError):
        dispatch_once(s, journal, decision, page, act=act,
                      is_fresh=lambda: True, interrupted=lambda: False)
    assert calls == [1] and journal.records()[-1]["phase"] == "unknown"
```

- [ ] **Step 3: Run** `uv run pytest tests/test_commit.py -q`; expect new approval/dispatch interfaces to fail.
- [ ] **Step 4: Implement proposal binding and commitment classification.** Hash service/account, action, route/recipient, items/quantities, timing, payment, all monetary terms and revision. Keep stable semantic line IDs when an equivalent fresh cart is extracted so cosmetic rerenders do not invalidate approval. Combine Jev CONFIRM, coordinator action/form assessment and code backstops; regex labels alone are insufficient. Any action whose external effects are unresolved pauses for assessment. Cover auto-submit typing, Enter, dialog accept and editing account settings, not just clicks. Authentication and ordinary preparation are distinct from consequential submissions. No arbitrary twenty-step approval grace period.
- [ ] **Step 5: Implement the dispatch boundary.** Check current decision ownership, cancellation/amendment revision, fresh target, fresh proposal and input/challenge scope immediately before mutation. If a commitment is approved, consume its approval, then durably append dispatch intent before issuing input. Recheck cancellation/revision immediately after the durable append and before issuing browser input; a cancellation received during the write must prevent dispatch and append a `not_dispatched` receipt. Once the browser call begins, any unclassified exception is unknown; only a verified pre-input `StalePage` means not dispatched. Record result/unknown before another observation. No retry, reload-based resubmit or second provider purchase while unresolved.

```python
execution_id = decision["id"]
if execution_id in state.executions:
    raise ValueError("decision already consumed")
if interrupted() or not is_fresh():
    raise ValueError("decision no longer executable")
# Approval and challenge checks occur before this transition.
record = {"execution_id": execution_id, "phase": "dispatching",
          "operation": decision["operation"], "choice": decision["choice"],
          "revision": state.revision, "challenge_id": challenge_id}
journal.append(record)
state.executions[execution_id] = {**record, "status": "pending"}
```

`dispatch_once` has no callback that can secretly repeat an input. The executor may perform an operation's defined sub-events (mouse press/release, typing a code group); ambiguous partial completion is unknown, never restart of that operation.
- [ ] **Step 6: Make journal failures safe and recoverable.** Use a task-owned opaque filename, parent directory mode 0700, `os.open` with private mode 0600 and no symlink following where available, a per-journal lock, redaction before JSON encoding, append and fsync. Records include task ID/revision, monotonic and wall-clock timestamps, origin, code-owned account reservation key, whether this is a commitment, and proposal hash where applicable. A sanitized task summary records the remaining objective/requirements. These records must be sufficient to restore an unresolved account reservation without storing secrets or making an old approval executable. Failure to persist intent prevents dispatch. If result persistence fails after dispatch, retain unknown state and stop mutations. Ignore only an incomplete final line on recovery; never infer that its action failed. Distinguish a browser `executed` receipt from a verified external `outcome`: an executed purchase without a recorded conclusive outcome is still unresolved after restart. `Journal.interrupted()` returns both unfinished dispatches and unresolved external commitments. Approval never survives process restart as executable authority.
- [ ] **Step 7: Test cancellation before dispatch, amendment during model generation, cosmetic rerender, changed charge with same total, missing evidence, result-read failure, crash after intent, disk-full intent write, partial browser input and duplicate decision IDs.** Check no second `act` call in every ambiguous case. Browser methods must distinguish stale-before-input from possibly-dispatched failure; retain existing low-level freshness/occlusion tests.
- [ ] **Step 8: Verify and commit.** `uv run pytest tests/test_commit.py tests/test_gate.py tests/test_browser_unit.py tests/test_traces.py -q`; Ruff. Commit `fix: bind approvals and journal browser execution once`.

### Task 9: Goal-derived completion and truthful transaction outcomes

**Files:** Modify `assistant/commit.py`, `assistant/coordinator.py`, `assistant/jobs.py`, `assistant/questions.py`; extend `tests/test_commit.py`, `tests/test_jobs.py`, `tests/test_talk.py`, `tests/test_events.py`.

**Interfaces:**
- `record_outcome(state: TaskState, execution_id: str, outcome: dict, *, now: float, journal: Journal) -> str` validates `{status, evidence_ids, artifact_id}` where status is pending_user/processing/success/failed/unknown, then durably records the outcome before releasing any account reservation.
- `completion_report(state: TaskState) -> dict` returns `{verified: bool, missing: list[str], evidence_ids: list[str]}`.
- `record_delivery(state: TaskState, artifact_id: str, *, message_id: str) -> None` records an actual channel delivery receipt.

- [ ] **Step 1: Write false-completion and undelivered-artifact regressions.**

```python
from assistant.commit import completion_report, record_delivery
from assistant.state import TaskState

def test_model_done_boolean_cannot_complete_an_invoice_download():
    s = TaskState("t1", "Download my latest invoice and send it here")
    s.completion = [{"id": "invoice", "description": "Current invoice delivered in chat",
                     "kind": "artifact", "material_keys": [], "evidence_ids": [],
                     "execution_id": None, "artifact_id": "file1"}]
    assert completion_report(s)["verified"] is False
    record_delivery(s, "file1", message_id="m1")
    # Delivery alone still does not identify this as the requested current invoice.
    assert completion_report(s)["verified"] is False
```

- [ ] **Step 2: Run** `uv run pytest tests/test_commit.py -q`; expect missing completion interfaces.
- [ ] **Step 3: Implement generic proof requirements.** At task creation, coordinator proposes requirements describing what evidence establishes this goal. Code validates requirement schema and retains it until a user amendment. A read-only comparison can complete without a transaction; an order cannot complete at cart review; an upload must establish the required file/submission result. Bind action evidence to this task's execution and later observation; an old order in account history is insufficient. Artifact requirements need code-owned download/upload receipt, matching goal context and chat delivery for requested downloads.

```python
def completion_report(state):
    missing, evidence = [], []
    for requirement in state.completion:
        if not requirement_verified(state, requirement):
            missing.append(requirement["id"])
        else:
            evidence.extend(requirement["evidence_ids"])
    return {"verified": bool(state.completion) and not missing,
            "missing": missing, "evidence_ids": evidence}
```

Define `requirement_verified(state: TaskState, requirement: dict) -> bool` in this module. It verifies valid provenance, task/revision/material linkage, relevant successful execution for action requirements, and owned artifact plus delivery receipt for artifact requirements. It must not accept an arbitrary nonempty quote or GPT boolean as proof.
- [ ] **Step 4: Implement outcome transitions.** “Finding a driver” is processing; matching-driver confirmation is success only if it satisfies the stated goal. A payment OTP is pending_user, bound to the approved transaction. An explicit declined payment is failed with evidence. Disconnects or absent receipts are unknown. Observe for at most 300 seconds using a supplied monotonic clock and bounded waits; at most one meaningful processing notice for an unchanged state. Do not automatically place a new order after failed/unknown outcome. A verified failure may invite the user's next choice.

```python
journal.append({"phase": "outcome", "execution_id": execution_id,
                "status": outcome["status"], "evidence_ids": outcome["evidence_ids"],
                "task_id": state.task_id, "revision": state.revision, "at": now})
state.executions[execution_id]["outcome"] = outcome["status"]
```

Serialize sanitized evidence provenance with the outcome so restart diagnostics can explain it. If this write fails, keep the reservation and report uncertainty rather than allowing conflicting new work.
- [ ] **Step 5: Add tests for old-account-history success, unrelated quote, wrong task's download, cancelled in-flight order that later succeeds, processing timeout, missing artifact, and delivered matching artifact.** Vary goal verbs so removing `rules.COMMERCE` as a completion authority is tested. A Jev DONE with incomplete proof continues observation or ends blocked, never done.
- [ ] **Step 6: Verify and commit.** Run changed pure tests and Ruff. Commit `fix: verify task outcomes and artifact delivery`.

### Task 10: Bounded progress, semantic recovery and field cache validity

**Files:** Modify `assistant/state.py`, `assistant/coordinator.py`, `assistant/interaction.py`; extend `tests/test_state.py`, `tests/test_coordinator.py`, `tests/test_interaction.py`.

**Interfaces:**
- `track_progress(state: TaskState, *, operation: str, semantic_state: str, change_evidence: tuple[str, ...], now: float) -> str` returns continue/wait/recover/blocked.
- `begin_recovery(state: TaskState, *, objective: str, semantic_state: str, recovery: dict) -> bool` allows at most two distinct unsuccessful recovery approaches at the same objective/state.
- `spend(state: TaskState, kind: str) -> None` increments/checks actions/jev/coordinator budgets; raises a descriptive `ValueError` when exhausted, before the corresponding request/action.

- [ ] **Step 1: Write the countdown/scroll-loop regression.**

```python
from assistant.state import TaskState, track_progress

def test_unproductive_scrolls_cannot_avoid_stall_detection():
    s = TaskState("t1", "Find a specific option")
    results = [track_progress(s, operation="SCROLL_DOWN", semantic_state="same-options",
                              change_evidence=(), now=float(i)) for i in range(3)]
    assert results[-1] == "recover"
```

- [ ] **Step 2: Run** `uv run pytest tests/test_state.py -q`; expect missing progress API failure.
- [ ] **Step 3: Implement meaningful progress and budgets.** Progress uses validated evidence of accepted input, auth transition, requested options, new relevant content or confirmed outcome. Countdown, animation and changed screenshot hashes do not establish progress. Detect repeated semantic state/action cycles, including LOOK and scrolling; WAIT uses elapsed time, not fast repeated decisions. Read-only wait scheduling belongs in the loop and yields to cancellation/user events. Keep counters and recovery keys in `state.progress` and diagnostics; do not reset them on question rewording.

```python
BUDGETS = {"actions": 150, "jev": 300, "coordinator": 60}
count = state.progress.setdefault("counts", {}).get(kind, 0)
if count >= BUDGETS[kind]:
    raise ValueError(f"{kind} budget exhausted")
state.progress["counts"][kind] = count + 1
```

- [ ] **Step 4: Implement cache freshness tests.** Two fields with the same host/label but different form purpose cannot share input caches. A changed helper input, task revision, challenge, constraints, selected provider or address invalidates cache. A stale-before-dispatch observation with equivalent full helper input can reuse the value without asking again. An uncertain execution cannot reuse it as a retry.
- [ ] **Step 5: Add tests at exact budget boundaries, repeated WAIT with fake clock, cyclic state changes, two failed recovery approaches, cancelled waiting and invalid coordinator response repair counts.** Assert blocked summaries identify the remaining objective and never ask the user which browser control to click.
- [ ] **Step 6: Verify and commit.** `uv run pytest tests/test_state.py tests/test_coordinator.py tests/test_interaction.py -q`; Ruff. Commit `fix: bound semantic recovery and invalidate stale field inputs`.

### Task 11: Integrate the authoritative Jev loop

**Files:** Modify `assistant/agent.py`, `assistant/model.py`, `assistant/jobs.py`, `assistant/questions.py`, `assistant/rules.py`, `tests/fakes.py`; create `tests/test_reliability.py`; update `tests/test_agent.py`, `tests/test_login.py`, `tests/test_wrong_data.py`, `tests/test_help.py`, `tests/test_talk.py`, `tests/test_gate.py`, `tests/test_look.py`, `tests/test_operations.py`, `tests/test_run.py` and `AGENTS.md`.

**Interfaces:**
- Preserve `Agent(url, goal, *, asker, notify, profile, vault, record_dir, ...)`, `command(name, body=None)`, `snapshot()`, `trace()`, `close()` and existing server command names. Add optional constructor keyword `task: TaskState | None = None` so a pre-browser conversation can pass its existing state without losing answers.
- Add `Agent.task: TaskState`, `Agent.secret_store: SecretStore`, `Agent.journal: Journal`, and `Agent.coordinate(event: str) -> None`.
- Retain the existing command adapter's ready/predicted state separately from `TaskState.status`; task lifecycle status is the spec's running/waiting_user/waiting_site/paused/blocked/done/failed/cancelled. Do not confuse “predicted” with a user-visible task lifecycle.
- `Agent.amend(text: str, changed_keys: set[str]) -> None`, `Agent.resume() -> None`, `Agent.stop() -> None` invalidate decisions as appropriate.
- `model.choose(state, goal, history)` remains the Jev entry point. `Agent.predict` adds a code-owned decision ID after validating the response, so every caller (including scripted test models) uses the same ownership rule. No provider-specific arguments.

- [ ] **Step 1: Add integration tests that pin Jev's authority.** Adapt `tests/fakes.agent_with` to initialize the new stores, coordinator stub and journal through one shared initializer also used by the real constructor. Do not create a second fake-only implementation. Test:

```python
from fakes import agent_with, decision
from assistant import agent as loop

def test_answered_choice_waits_for_a_fresh_jev_decision(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{"option": "home"}])
    decisions = []
    def choose(page, goal, history):
        decisions.append(goal)
        return decision("wait", "WAIT")
    monkeypatch.setattr(loop, "choose", choose)
    # The answer is already a fact; a future selection must use the normal loop.
    from assistant.state import put_fact
    put_fact(a.task, "address", "Home", source="user")
    a.command("tick")
    assert len(decisions) == 1
    assert all(call.args[0] == "WAIT" for call in a.browser.act.call_args_list)
    assert "Home" in decisions[0]
```

Add a second test where Jev deliberately chooses ASK_USER for an already-satisfied phone requirement: no question is emitted, no TYPE_TEXT is substituted, and recovery/redecision follows. Assert unused target heads never execute.
- [ ] **Step 2: Run targeted tests to see old behavior fail.** Use `uv run pytest tests/test_reliability.py tests/test_login.py tests/test_wrong_data.py -q`. Replace old login-banner tests with the Task 6 scenarios before expecting green; retain coverage for actual required login.
- [ ] **Step 3: Wire observe and coordinate.** `observe` sanitizes then assigns evidence IDs. After relevant events, call the coordinator, apply valid facts/requirements/auth/outcome changes, and build Jev context from the original goal plus structured constraints. Ordinary browser steps do not call the coordinator unnecessarily. State transitions are visible in traces. Starting URL selection honors unresolved provider/location requirements before navigating; root URLs are preferred to invented deep links.
- [ ] **Step 4: Wire decision and dispatch.** For each decision: validate operation and its own head; resolve only observed target refs; apply required-choice, input, secret, cancellation and commitment checks; dispatch once; journal result; observe; assess relevant outcome. Use `dispatch_once` rather than duplicating journal/approval logic. Check freshness after helper generation. Field scope includes form, challenge, origin and task revision. An OPEN_URL receives only validated URLs; uploads resolve code-owned media IDs, never model paths.

```python
# Inside the validated executable-action path; the selected action comes from page["actions"].
receipt = dispatch_once(
    self.task, self.journal, decision, page,
    act=lambda: self.browser.act(operation, action, page, **inputs),
    is_fresh=lambda: self.browser.fresh(page, action),
    interrupted=self.interrupted,
    proposal=self.task.proposal if requires_commit else None,
    challenge_id=challenge_id,
)
# Observe only after dispatch_once has recorded receipt/uncertainty.
```

Define the local `requires_commit`, `challenge_id` and `inputs` from validated coordinator/form context and resolver results; never model-executable code. For unknown receipts, only bounded reconciliation may continue.
- [ ] **Step 5: Remove conflicting shortcuts and policies.** Delete the production use of `type_profile_detail`, `click_same`, direct visual clicks from `look`, CAPTCHA click loops, `code_form` newest-unused secret selection, revisit-as-rejection logic in `previous`, card-save prompts, `approval_valid` amount-presence authority, and `commerce_goal` as completion authority. `ASK_USER`, `CONFIRM`, `DONE` and `BLOCKED` invoke the new semantic/question/verification gates. Auth or choice answers become facts and Jev chooses the subsequent action. Preserve COPY/DRAG/files/frames/dialog operations already covered by browser tests.
- [ ] **Step 6: Replace outside-chat help and guessed geolocation.** Remove guide/device help rungs that ask the customer to operate an app/browser; keep useful explanatory messages, observed browser alternatives and an honest blocker. Do not pass the first stored address's coordinates to `Browser` unless it is the explicitly selected location for this task. Current-location requests without user-provided location require a chat address/landmark.
- [ ] **Step 7: Integrate cancellation, completion and redaction.** Before model work and dispatch, check updates/cancel. Jev DONE calls `completion_report`; only verified requirements allow done. Process an in-flight result read-only after cancellation. Release/discard secrets on expiry, replacement, completion/cancel; sanitize retained records before removing redaction copies. Keep resumably blocked tabs open for Core's lifetime policy. Record unknown external outcomes even if task status becomes cancelled.
- [ ] **Step 8: Update all superseded tests and instructions together.** Replace assertions that expect automatic login, stored CVV, direct chat-choice clicks, device takeover and model-only completion with the approved behavior. Keep valid browser, file, masking and protocol tests. Point `AGENTS.md` at the approved reliability spec and preserve its Jev/execution/Chrome/test rules. Do not edit historical plans to appear implemented by this change.
- [ ] **Step 9: Verify the complete offline suite and commit.** `uv run ruff check .`, `uv run pytest`, `node --check assistant/snapshot.js`, `node --check assistant/static/app.js`. Inspect failures rather than weakening safety assertions. Commit `refactor: integrate structured coordination into the Jev loop`.

### Task 12: Ordered chat replies, task resumption and lifecycle

**Files:** Modify `assistant/core.py`, `assistant/channel_web.py`, `assistant/messages.py`, `assistant/server.py`, `assistant/static/app.js`; extend `tests/test_core.py`, `tests/test_channel.py`, `tests/test_chat_page.py`, `tests/test_chat_server.py`, `tests/test_messages.py`, `tests/test_traces.py`.

**Interfaces:**
- Keep `Core.receive_webhook(body)`, `receive(event)`, `showing()` and existing channel payloads. Normalized events add `question_id: str | None` and `secret_reply: bool`; validate these before logging/classification.
- `showing()` returns `{id, secret, message_id, challenge_id}` for the active question.
- `Core.resume(task_id: str) -> None`, `Core.reap_idle() -> None`, `Core.recover_interrupted() -> None` manage same-process resumable tasks and startup diagnostics.
- `Core.reserve(task: Task, origin: str, account: str | None) -> bool` records task-owned account/site reservations; conservative site serialization applies while account identity is unknown.
- `Core.classify` defaults to `coordinator.interpret_message` and consumes its validated schema, removing Jev chat intent calls. Existing constructor injection hooks remain available for tests with adapters updated once.
- `core.Task.flow: TaskState` is created before opening any browser and passed as `Agent(task=task.flow)`. Extend `jobs.start(goal: str, facts: dict | None = None) -> tuple[str, str, dict]` without changing its return contract; it receives resolved provider and comparison constraints, never guesses unresolved user choices.

- [ ] **Step 1: Add duplicate delivery and stale-secret regressions.** Use the existing `world` fixture and adapt its scripted classifier to the new schema. Add:

```python
def test_duplicate_inbound_id_starts_one_task(world):
    event = {"from": "local", "id": "same-id", "type": "text", "text": "Find options",
             "context": None, "question_id": None, "secret_reply": False}
    world.core.receive(event)
    world.core.receive(event)
    assert len(world.core.tasks) == 1
```

For stale secrets, display question A, mark it answered/expired, display B, then deliver a secret event naming A. Assert B remains unanswered, neither classifier nor logs contain the value, and the user receives a concise stale-question acknowledgement. Add a test that a waiting secret question never preempts an already displayed ordinary question.
- [ ] **Step 2: Run** `uv run pytest tests/test_core.py tests/test_channel.py -q`; expect duplicate/preemption/identity failures before implementation.
- [ ] **Step 3: Serialize inbound handling before model calls.** Add a dedicated inbound mutex/queue, separate from Core's scheduling lock, that preserves accepted arrival order through routing. Deduplicate event IDs before side effects. Preserve current direct routing for explicit reply IDs; validated secret replies bypass all model calls. Check stale ID and `secret_reply` metadata before appending user text to any log. Keep the input event ID if transport retries a failed delivery. Do not hold the scheduling lock during network/model calls. Explicit cancel interrupts promptly via the same ordered event path and pre-dispatch cancellation token.

Initialize `Task.flow` at task creation and run the coordinator's start assessment against an empty observation before constructing `Browser`. Resolve provider/default/comparison and any essential location question through `Core.ask` using the same task state. Only then call `jobs.start` with those facts and allocate the task's browser. A task answered entirely in chat creates no browser. Provider discovery for a requested comparison can itself be a browser objective, but the unresolved preferred-provider-or-compare choice cannot silently become a guessed starting provider.
- [ ] **Step 4: Send question identity from the local chat.** Track the displayed question separately from password mode; include it on typed replies and files, capture it when the message is submitted, and keep secret input masked/cleared on success or failure. A stale secret submission must remain classified as secret even after a polling update changes the open question.

```javascript
const submittedQuestion = openQuestion;
const message = {
  type: 'text',
  text: {body: text},
  x_question_id: submittedQuestion?.id ?? null,
  x_secret_reply: submittedQuestion?.secret === true
};
```

Define `openQuestion` from the server's `open` snapshot. Normalize these fields in `WebChannel.events`. For an explicit non-secret reply, use GPT to distinguish an answer from an amendment/new task when needed, while retaining the captured question identity; do not force “start another task” into the current address field. Secret cancel is recognized without a model. Old buttons cannot act on a new question.
- [ ] **Step 5: Fix queue timing and presentation.** `show_next` chooses among queued items only when no question is open. Priority can choose the next question, never preempt. Set `displayed_at` once all question messages have been sent, and use that timestamp for 15-minute pause/24-hour cancellation; queued questions do not age as displayed. Partial answers keep only missing requirements. Routing ambiguity uses a tracked clarification bound to the captured question/task, not untracked buttons or a second simultaneously active question; retain the original unresolved requirements until clarification resolves them. Secret challenges that expire while queued are refreshed when the website actually needs a new challenge; do not ask for an already expired code.
- [ ] **Step 6: Integrate scheduling, blocked tasks and same-process resume.** Core tracks spec lifecycle states. Waiting/paused tasks retain browser state. Blocked tasks release active execution slots; unknown external commitments retain account reservations. A provider switch or auth redirect updates reservations before access, not just initial URL. A resumed task reacquires capacity/reservations, observes freshly, invalidates stale proposals and reconciles unknown outcomes before any mutation. Use deterministic acquisition order or relinquish unused non-pending reservations to avoid multi-provider deadlock. Pending commitments are never released merely to break contention. Explicit cancel closes tabs after bounded in-flight reconciliation; idle retained tabs close after 24 hours.
- [ ] **Step 7: Add startup interruption reporting and file receipts.** `recover_interrupted` reads journal summaries, reports interrupted/unknown runs without starting browser agents, and reserves unresolved accounts against a conflicting fresh task. No approval or secret is resumed. A user-requested reconciliation of a prior interrupted task opens a fresh read-only inspection, checks the current account's relevant order/booking history, and updates its outcome only with matching evidence; no automatic reconstruction/replay of the old task. If proof remains unavailable, retain the reservation and explain the blocker. `deliver_files` records returned channel message IDs and calls `record_delivery` only after actual registration/send succeeds. A failed delivery leaves the completion requirement open and can retry the channel delivery without repeating the browser download/transaction. Sanitize error text before chat, journal and logs. Handle explicit local default/password-consent/legacy-cleanup choices in the same ordered question system, without confusing them with browser actions.
- [ ] **Step 8: Test simultaneous inbound messages with thread barriers, delayed classifiers, partial replies, old buttons, absent/forged question IDs, expiry while queued, blocked/resumed slot release, account collisions after redirects, unresolved-order restart, and delivery failure.** Use fake clocks and temporary journals. Cover HTML in replies/options as plain text and composer contents absent after secret submission. Update the existing test that reused one tap ID for different human clicks; each distinct click gets a distinct ID, transport duplicate retries reuse it.
- [ ] **Step 9: Verify and commit.** `uv run pytest tests/test_core.py tests/test_channel.py tests/test_messages.py tests/test_chat_server.py tests/test_traces.py -q`, `uv run pytest -m browser tests/test_chat_page.py -q`, both JavaScript syntax checks and Ruff. Commit `fix: bind chat replies and preserve resumable task state`.

### Task 13: Whole-flow browser regressions and metamorphic coverage

**Files:** Extend `assistant/fixtures/reliability.html`, `tests/test_reliability.py`, `tests/test_chat_e2e.py`, `tests/test_e2e.py`, `tests/test_shop.py`, `tests/test_frames.py`, `tests/test_events.py`, `tests/test_traces.py`, `tests/conftest.py`.

**Interfaces:** Use the real `Agent`, `Core`, channel/server and throwaway-browser fixtures. Only model/network responses, clock and test-site behavior are scripted. `tests/reliability_fakes.py` may provide `ScriptedText(responses: list[dict])` with `__call__(system, payload, **kwargs) -> tuple[dict, dict]`; it records sanitized requests and raises on unexpected extra calls.

- [ ] **Step 1: Add an exact scripted model helper.**

```python
class ScriptedText:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, system, payload, **kwargs):
        self.requests.append((system, payload, kwargs))
        if not self.responses:
            raise AssertionError("Unexpected additional model call")
        return self.responses.pop(0), {"model": "scripted", "latency_ms": 0, "usage": {}}
```

Use real schemas from the coordinator/field/proposal code; do not patch internal gates or completion assertions away. Jev responses select observed indexed targets, never fixture-specific production selectors.
- [ ] **Step 2: Add end-to-end fixture cases for the six reproduced defects.** Sign-in link persists after actual authentication; Code/CVV fields coexist; valid typed field remains visible without rejection; old ₹79 item coexists with new ₹95 total; numeric CAPTCHA remains text; DONE without artifact/evidence is blocked. Assert questions, selected operations, ledger and final chat, not only final status. Repeat relevant cases through `/webhook` and `/outbox` to prove reply binding.
- [ ] **Step 3: Add one complete shopping flow and one complete ride flow.** Each has initial provider/address choice, optional comparison requiring two sequential provider-specific challenges, meaningful options, a user choice, final review, one confirmation, optional payment challenge and independently observable result. Add negative variants: no fresh login required, skip provider, promotion threshold missed, more items cost more overall, cart fee changes after approval, user declines, unsupported outside-chat authentication, pending driver then timeout. These are test goals, never runtime task classes.
- [ ] **Step 4: Add generic held-out fixture families.** A read-only lookup, a message/form submission, a cancellation/account change, and a file upload/download exercise the same runtime without branch-by-site logic. A resumed blocked task retains known answers and does not replay an earlier mutation. Test cancellation while input is in flight and transport failure immediately after an accepted order.
- [ ] **Step 5: Add metamorphic variants.** Fixture query parameters change label wording, DOM order, duplicate field labels, delayed validation, fee label, frame origin, countdown and rerender timing while preserving task meaning. Parameterize existing scenario assertions over variants. The script supplies different observed targets as needed; production code must remain unchanged across variants.

```python
import pytest

@pytest.mark.browser
@pytest.mark.parametrize("variant", ["base", "reordered", "renamed", "delayed", "frame"])
def test_reliability_fixture_exposes_the_same_total(browser_at, variant):
    b = browser_at(f"/reliability.html?case=checkout&variant={variant}")
    page = b.observe()
    assert "Total ₹95" in page["text"]
```

This fixture test is only a setup check; the full scenario must additionally prove the agent uses the new total and asks for refreshed approval, with no duplicate purchase. Keep fixture support deterministic and local.
- [ ] **Step 6: Assert redaction and external-effect invariants across every scenario.** No raw secret in any model payload, retained journal, diagnostics, screenshot or completed-task object. No direct Python action after a chat answer, no model selector/coordinate execution, no automatic retry after unknown outcome, no unnecessary repeated question and no false done. Assert every dispatch links to one Jev decision and every commitment to one consumed proposal approval.
- [ ] **Step 7: Run the required repository checks once after integration.**

```sh
uv run ruff check .
uv run pytest
uv run pytest -m browser
node --check assistant/snapshot.js
node --check assistant/static/app.js
```

Inspect test counts and results; a skipped Chrome suite is not a passing browser suite. Fix failures with a regression and rerun affected checks, then complete remaining required checks. These tests must still refuse paid API calls via `tests/conftest.py`.
- [ ] **Step 8: Commit.** Stage only changed fixture/test files and any specific runtime fix verified by its regression. Commit `test: cover general browser task reliability end to end`.

### Task 14: Operational docs, declared live evaluation and acceptance

**Files:** Create `docs/reliability-evaluation.md`; modify `docs/smoke.md`, `docs/known-issues.md`, `README.md`, `assistant/server.py` (trace summaries only if needed), `tests/test_traces.py`; update this plan's checkboxes with actual completion evidence.

**Interfaces:** Preserve existing `uv run jev`, `jev chrome`, `jev probe` and `jev traces`. Trace summaries add observed counts for coordinator/Jev/helper calls, questions/reasks, mutations, outcome state, elapsed time and verification evidence. Never label fixture counts as live reliability.

- [x] **Step 1: Write current operating instructions.** Remove instructions to save CVVs/cards, answer native Keychain prompts, or operate banking apps. Explain local text chat, dedicated agent Chrome, explicit defaults, opt-in comparison/multiple OTPs, complete fee review, final confirmation, blocked/resume and restart interruption behavior. Retain current HTTP protections, uploads/downloads and configuration. Mark historical known issues fixed only with the regression/test evidence; unresolved items stay open.
- [x] **Step 2: Add a concrete evaluation record format.** `docs/reliability-evaluation.md` must include this schema and separate fixture/live sections:

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

This is a documented input/output schema, not a fabricated result. Actual records must contain real commit IDs, trace IDs and observations. Use `not_run` until a run starts; then report completed/blocked/cancelled/failed with external outcome tracked separately. Count all attempted runs and record regressions instead of discarding unsuccessful attempts.
- [ ] **Step 3: Declare the live set before execution.** Reuse the smoke examples as candidate goals, add unrelated user-selected holdouts, and state which are comparisons/preparation versus real external commitments. Include fresh login/session reuse, provider omitted/comparison chosen, recovery and a file task. Do not assume the user wants actual test purchases or ride bookings merely because implementation is approved. Obtain the user's missing locations/accounts/holdout goals through chat when required; each final transaction still requires its own concrete approval. Use the declared goal's actual success condition, not a quote screen.
- [ ] **Step 4: Launch live evaluation separately when explicitly authorized.** Start only the dedicated agent Chrome and local server, with real keys read from ignored `.env` without printing them. Run through the chat and the implemented agent. The developer must not complete the website task manually or patch in selectors/site scripts to make a run pass. User supplies OTPs and approvals in chat. A supported user correction is normal input; developer intervention is recorded as a failed run requiring a fix and rerun. If evaluation cannot run yet, report the implementation as locally verified with live acceptance pending; leave this step unchecked.
- [ ] **Step 5: Verify outcomes and close regressions.** Independently inspect the browser/page artifact or receipt through the agent's own evidence and traces; check requested product/route/recipient/quantity/destination and actual charge. A finding-driver page is not a completed cab goal if a confirmed driver was required. Downloads must arrive in chat. External CAPTCHA/app-only/site-blocking limitations count as recorded blockers, not successful completions. Any duplicate/unauthorized action, secret disclosure, false success or unnecessary repeated question blocks acceptance until fixed and freshly reevaluated.
- [ ] **Step 6: Report honest acceptance results.** Include tested commit, fixture checks, attempted live sample size, successful feasible goals, blockers, unknown outcomes, model calls/latency and unresolved defects. Every declared feasible acceptance goal must have a verified successful run; unresolved product failures remain work. Make no claim that a finite set proves every website works. Do not mark the whole milestone complete while live evaluation is pending.
- [x] **Step 7: Verify docs/summary changes and commit.** Run `git diff --check`, `uv run pytest tests/test_traces.py -q` if trace code changed, and Ruff for changed Python. Rerun broader checks only for relevant new runtime changes. Commit `docs: document reliability validation and live results` when actual work/results are recorded; do not populate success records in advance. Never push.

Task 14 local evidence (2026-09-25): runtime commit `0951818ab776610796fc17e3afde9a3db3bbc5b5`
adds the narrow current-page PDF export receipt, with eight real-Agent/Chrome tests. Ruff, 718 offline tests,
171 browser tests, both JavaScript syntax checks and diff checks passed. README, smoke instructions,
known issues and `docs/reliability-evaluation.md` record the local scope and ten not-run candidates.
No paid APIs, live transactions or push. Steps 3–6 remain open: exact user parameters/holdouts are not yet
supplied, live evaluation is not authorized, and no live outcome or acceptance has been verified. General
invoice/download attribution, server upload acceptance and complete helper/reask accounting remain open.
The initial failed browser attempt and successful reruns are retained in the evaluation record.

## Dependency and acceptance map

| Approved spec requirement | Owning tasks and acceptance evidence |
|---|---|
| Generic local, text/chat-only scope and model separation (§1–3) | 3, 6, 11–14; prompts, no direct-action tests, actual chat flows |
| Structured task contract, facts, revisions and evidence (§4) | 1, 3, 9, 11; dependency/provenance and malicious-page tests |
| Indexed observation, Jev target authority, visual refs (§5) | 2, 3, 11, 13; freshness/frame/unused-head/visual tests |
| No repeated questions, rich options/updates, provider/default/comparison (§6) | 4, 7, 12, 13; explicit defaults, OTP notice, partial replies, fee/promotion flows |
| Login, scoped secrets, CAPTCHA, outside-chat blockers (§7) | 5, 6, 11–13; six defect regressions plus secret isolation/masking |
| Complete cart, exact proposal, approval and one dispatch (§8) | 7, 8, 11, 13; fee arithmetic, proposal revalidation, uncertain execution |
| Bounded progress/recovery and truthful completion (§9) | 9, 10, 11, 13; fake-clock bounds, goal proof and artifact delivery |
| Cancellation, resume, account reservations, journal/restart (§10) | 8, 11, 12, 13; race/crash/unknown-outcome tests |
| Deterministic plus declared live acceptance (§11) | 13–14; required commands, independent live evidence and failure accounting |

## Execution handoff

Implementation begins after the user reviews this plan and chooses the execution method. Recommended: **subagent-driven**, with one implementer and one independent review per task, because secrets, concurrent replies and transaction dispatch cross several interfaces and a regression can expose information or duplicate a purchase. Tasks follow dependencies; this recommendation does not authorize parallel changes to shared files.

Alternative: **native execution**, one agent implements the tasks sequentially in this session, followed by a fresh whole-branch review. It uses less context but defers independent review until the end.

Neither approach changes approval requirements inside the product or authorizes live bookings/purchases during development. Existing user changes remain untouched, task commits stay local, and the final report distinguishes implementation checks from real-service evaluation.
