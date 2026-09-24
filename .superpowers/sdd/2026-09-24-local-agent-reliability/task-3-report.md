# Task 3 implementation report

Base: `5d5fb52c91c0e23cc01e32a89d9815c259d626b5`.
Commit message: `feat: add event-driven GPT task coordination`.

## Result

Added `coordinator.py` with strict semantic assessment schemas, one bounded schema repair, event detection, validated assessment caching, 60-call budgeting, safe diagnostics, chat interpretation and visual annotations. Added prompts, a staged `jobs.visual_annotations` wrapper, and `model.TextJSONError` (a backward-compatible ValueError subclass carrying safe model metadata). No browser mutations or paid model calls were used by these tests.

Only facts, the semantic objective, permitted completion requirements, and descriptive progress apply here. Requirements, authentication observations, proposals and outcomes are validated and returned for their owning modules to apply. Approval, execution and task success are never coordinator writes. Original goal and constraints remain in state and every public task context; page facts cannot overwrite user facts.

## RED/GREEN evidence

- First `uv run pytest tests/test_coordinator.py -q`: exit 2, expected `ModuleNotFoundError: No module named 'assistant.coordinator'` during collection.
- Metadata/network cycle: `uv run pytest tests/test_coordinator.py tests/test_model.py -q`: 2 failed, 51 passed; failures proved missing `TextJSONError` and missing malformed-response metadata. Implemented dedicated schema exception. Initial focused GREEN: 61 passed.
- Nested validation/dialog/CAPTCHA/staged-job cycle: 4 failed, 27 passed; failures were invalid fact-key AttributeError, unchanged dialog signature, accepted known CAPTCHA, and missing `jobs.visual_annotations`. GREEN after implementation: 72 passed.
- Redaction/stale completion/nonfinite JSON cycle: 5 failed, 22 passed. Cached objective leaked a registered secret, completion applied after constraints changed, and fact JSON accepted NaN/Infinity/executable fields. GREEN after implementation: 77 passed.
- Applied-fact event cycle: targeted test failed because semantic writes caused a spurious stage change; after updating the last assessed signature on application, focused suite: 78 passed.
- Valid-context cycle: targeted test failed because invalid facts remained in the model prompt. Filtering invalid facts/stale evidence produced final focused GREEN: `uv run pytest tests/test_coordinator.py tests/test_jobs.py tests/test_model.py -q` — 79 passed in 0.19s.

## Required checks

- `uv run ruff check .`: passed.
- `uv run pytest`: 380 passed, 91 deselected in 10.09s.
- `uv run pytest -m browser`: 91 passed, 378 deselected in 64.82s. This ran before the final two coordinator-only prompt/event refinements; those APIs remain staged and have no browser call sites. Both refinements received targeted RED/GREEN and a full offline rerun.
- `node --check assistant/snapshot.js`: passed.
- `git diff --check`: passed.

## Design decisions and integration notes

- `SCHEMA` is the canonical wire description. All top-level and structured nested records reject unknown/missing fields; integers reject bool, timestamps reject bool/nonfinite values, and fact JSON rejects executable field names. Facts accept only page/derived provenance from GPT, never user/profile/system authority.
- Evidence references must belong to the latest observation and current revision. This is deliberately conservative: unchanged observations with no evidence-bearing outputs reuse cache; an evidence-bearing cache entry from a previous observation is revalidated and reassessed instead of silently rebinding IDs.
- Cache keys include revision, semantic observation, constraints/facts/requirements/questions, challenge/auth state, proposal and approval. Timer normalization is deliberately narrow (explicit resend/retry/expiry/remaining seconds wording), preserving monetary amounts and quantities.
- Public dialog type/message participate in the signature and prompt because Task 1's retained observation currently omits dialogs.
- Completion writes require the exact assessment issued for start/amendment at the still-current semantic signature; a model boolean never marks the task done.
- Proposal items and later terms are semantic string lists in this staged schema. Monetary lines have the documented Task 7 structure; arithmetic/extraction validation is explicitly left to the commitment owner. Requirement options use `{id, label, description, evidence_ids}` pending Task 4's owning implementation.
- Auth allowed origins must already be observed; this does not establish verified redirect authority. Task 6 must enforce flow/submission/redirect relationships before applying observations or releasing secrets. New challenge IDs must be created by code before GPT references them.
- Artifact references accept code-owned delivered IDs or future `artifact_records` observation entries, never arbitrary model paths. Task 9 should bind the final receipt store into this lookup.
- Message interpretation returns a clarification for unknown task IDs; it never creates tasks. Explicit reply IDs/secrets remain the routing caller's responsibility in Task 12.
- Visual annotations accept only supplied cell IDs and short label/text; known CAPTCHA pages/challenges reject before a call, and the visual prompt refuses challenge solving. Task 2 browser validation remains responsible for screenshot freshness and geometry. Task 6/11 must identify challenges before invoking this API.
- Existing `model.classify`, `jobs.look` and agent callers remain intact until Tasks 11–12 remove legacy routing/visual shortcuts. No rollout claim is made here.
- Registered secrets are redacted from assessment payloads, rejected repairs, returned/cached values and diagnostics. Transport failures are separate from schema failures and receive no schema retry. Raw malformed JSON is never retained; safe metadata survives.
- Root AGENTS.md references RTK.md, but no RTK.md exists in the worktree or repository. Parent confirmed proceeding with available AGENTS.md and approved design/plan.

No known failing checks. No live-model reliability claim.
