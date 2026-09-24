# Task 4 implementation report

Base: `829438ed32ced20825104f4f59403326d9de75cb`.
Commit message: `feat: reuse answers and clarify provider choices in chat`.

## Result

Added browser-independent `interaction.py` with semantic requirement identity, code-owned stable question IDs, partial answer reuse, provenance, exact material-choice matching, dependency invalidation, provider policy, and evidence-backed notice deduplication. Added explicit consent-marked public defaults to Profile. Long reviews retain all charge lines; full option descriptions survive short button/list labels in plain text. Coordinator rejects duplicate/ambiguous requirement and option identities before passing them to the owner. Existing agent/core callers remain intact for Tasks 11–12.

## Exact RED/GREEN evidence

- Initial `uv run pytest tests/test_interaction.py -q`: exit 2, expected missing-interface collection failure `ModuleNotFoundError: No module named 'assistant.interaction'` (1 error in 0.10s). First implementation GREEN: 15 passed in 0.15s.
- `uv run pytest tests/test_interaction.py tests/test_profile.py tests/test_messages.py tests/test_jobs.py -q`: RED **7 failed, 46 passed in 0.73s**. Failures: historical provider incorrectly satisfied an explicit choice, changed-price answer stayed valid, cosmetic refresh retained stale option evidence, missing explicit-default API (two tests), truncated option semantics, and eight-line charge truncation. GREEN: **53 passed in 0.63s**.
- `uv run pytest tests/test_coordinator.py -q`: RED **4 failed, 40 passed in 0.27s** for duplicate option IDs, duplicate requirement IDs, duplicate fact keys, and options attached to non-choice kinds. After strict validation, prescribed targeted GREEN: **87 passed in 0.69s**.
- `uv run pytest tests/test_interaction.py -q`: RED **2 failed, 19 passed in 0.17s** for dependency answers surviving amendment/reopen. Added fact dependency provenance and transitive reopen invalidation. Prescribed targeted GREEN: **89 passed in 0.73s**.
- `uv run pytest tests/test_profile.py -q`: RED **1 failed, 16 passed in 0.60s** because a hidden phone field could be saved as a plain public default. Reject hidden profile keys at this public-default boundary. Final prescribed targeted GREEN: **90 passed in 0.70s**.
- Ruff initially caught one overlong new line; wrapped it. Final Ruff is green.

## Checks

- `uv run pytest tests/test_interaction.py tests/test_messages.py tests/test_profile.py tests/test_coordinator.py -q`: **90 passed in 0.70s**.
- `uv run ruff check .`: passed.
- `uv run pytest`: **422 passed, 91 deselected in 10.13s**.
- `uv run pytest -m browser`: **91 passed, 419 deselected in 64.56s**. This run overlapped the final dependency/hidden-default unit regressions; those staged interfaces have no browser callers. The final offline run includes all changes. Existing real-browser `test_message_text_is_never_html` passed, as did the full browser suite; app DOM rendering was unchanged.
- `node --check assistant/snapshot.js`: passed.
- `node --check assistant/static/app.js`: passed.
- `git diff --check`: passed.

No paid API calls, live-site tests, or everyday Chrome interaction. Browser tests use existing throwaway headless fixtures. No known failing checks.

## Decisions and staged integration concerns

- Information requirements live in `state.requirements` with kind text/choice/secret/file/image; legacy completion records there remain distinguishable by kind. `require` rejects ID collisions with different meanings. The canonical requirement is matched by purpose/scope/sorted fact keys regardless of wording or later alternate IDs. Consumers must use returned canonical `question.requirement_ids`, retaining existing requirement IDs when proposing later dependencies.
- The Task 3 wire schema is preserved exactly: options contain id/label/description/evidence_ids, with all material semantics in label/description. User choices store those semantics and evidence, never the transient option ID or a browser target. Latest-observation evidence is required at answer time. A cosmetic option refresh can rebind identical semantics while retaining the displayed reply ID; changed semantics close the old question. A previously answered choice reopens when its meaning is no longer present.
- `answer_requirements` accepts a mapping of named fact keys, never ambiguous free text. Task 12 must route/clarify free text before calling it. Partial answers retain provenance individually and leave only unresolved keys in the question text. It returns this question's missing requirement IDs. Duplicate replies do not overwrite accepted facts; unknown/task-mismatched/stale IDs fail. Mutations are atomic when a multi-key reply has invalid data.
- Only related **undisplayed text requirements** are combined automatically (same purpose/scope). Provider/address can combine when both use purpose/scope `task`. Displayed, secret, file/image, and material-choice questions do not expand. `displayed_at` stays None until the channel actually displays the question; Task 12 owns one-active-question scheduling and timers.
- Reopen reasons are the exact approved spec phrases: `expiration`, `observed validation failure`, `changed request`, `invalid format`, `genuine ambiguity`. Observed validation failure needs current evidence. Answer fact dependencies are derived from dependent requirements' keys, so amendment/reopen invalidates dependent answers without discarding unrelated facts.
- Secret handling is deliberately an opaque-reference boundary here. Raw secret answers are rejected. Task 5 must mint/validate the reference against task, purpose, origin, challenge, expiry and field permissions, and attach `question.challenge_id`; this module does not release credentials, resolve opaque references, or execute browser actions.
- Provider mode consumes user-owned `constraints.provider` / `constraints.compare`, then valid task-scoped source=user facts, then an applicable `{value, source: 'user', consent: true, scope}` default. Compare requests return the separate-login/multiple-OTP notice without another question. Free-text provider names are accepted without a whitelist; `provider='compare'` is the explicit comparison answer. Provider history does not satisfy the explicit provider requirement. No default is created by provider selection, and provider skip/unavailability records in progress remain untouched.
- `Profile.set_default` records explicit consent/source with task-wide applicability. Scoped defaults supplied to provider_mode may use `constraints.provider_scope`; callers must resolve applicability before use. Free-form preferences remain suggestions and are not migrated into defaults. Sensitive/hidden keys are rejected at the new public-default API; the existing profile/vault compatibility APIs remain for Task 5 to replace.
- Provider selection is **not** provider identity verification or credential permission. Tasks 6/11 must observe candidate identity/availability before secret release, serialize provider login questions, retain skipped/incomplete results, and collect unknown location in chat. No location APIs or site-specific navigation were added.
- `chat_update` accepts the coordinator notice shape. Factual notices require current evidence; deduplication uses revision/kind/semantic evidence (origin/kind/quote)/requirement IDs, so unchanged rereads or rewording do not spam chat. The `start` kind is code-derived from the immutable goal, ignoring unverified supplied prose. Provider-mode notices announce defaults/comparison; Task 11 should present them with the understood-goal start message. This helper does not mark an outcome verified; the completion owner must validate the outcome before issuing final success text.
- Coordinator `apply_assessment` still does not apply requirements/notices: Task 11 must call the corresponding owners after validated assessment. This preserves Task 3's staging and existing callers.
- `jobs.confirm_card` now preserves all returned charge lines, but it remains the legacy helper. Full arithmetic/material proposal verification belongs to Task 7 and rollout to Task 11. `messages.render` retains full option descriptions, splits long text before the actionable question, and preserves the supplied question ID. An existing long-label test was adjusted to allow the newly preserved label summary while still asserting text-first order and channel title limits.
- Root instructions reference RTK.md, absent as confirmed by the parent. Read available AGENTS.md, both approved designs, Task 4 brief, and the TDD skill/reference. No task outside Task 4 was implemented and no live-reliability claim is made.

## Review follow-up: evolving dependencies and explicit material choices

Both Important findings were reproduced and fixed:

1. Canonical requirements now reconcile dependency changes. Added/replaced prerequisites close obsolete questions, record a genuine-ambiguity reopen, and invalidate accepted answers and their downstream facts. Missing prerequisites block the dependent question until ready. Dependency removal drops obsolete fact provenance while preserving an otherwise valid answer; order-only changes preserve answers. Requirement cycles, including through a reworded canonical ID, are rejected before updating the existing record. Facts reused from explicit instructions acquire requirement dependency provenance, so subsequent amendments/reopens invalidate them correctly.
2. Explicit current user fact strings resolve only against a unique full observed option label (case/outer whitespace normalized). The selected fact retains its source and receives the full option label/description and current evidence. Shared-word/prefix matches, duplicate labels with different material details, and history still require clarification. Explicit profile-default facts use code-owned `explicit_default=True` metadata; the caller must set that only after validating saved consent and applicability. Ordinary source=profile facts and page-derived option facts never become user consent. The coordinator wire schema cannot emit this metadata, and Task 11's profile adapter must establish it when importing an authorized default. Answers still create facts only; no browser actions were introduced.

Exact regression evidence:

- RED: `uv run pytest tests/test_interaction.py -q` — **5 failed, 24 passed in 0.26s**. Failures: pending question ignored added prerequisite, accepted/downstream answer survived dependency addition, removed prerequisite retained obsolete provenance, explicit user string was reasked, and explicit saved-default string was reasked. Existing ambiguous/history cases remained passing.
- First GREEN: `uv run pytest tests/test_interaction.py -q` — **29 passed in 0.16s**.
- Additional boundary RED: same command — **2 failed, 30 passed in 0.24s** for a cycle through a canonical requirement and page-derived option meaning accepted as a user choice. The dependency-order/reused-instruction provenance regression also ran and passed.
- Final targeted GREEN: `uv run pytest tests/test_interaction.py tests/test_messages.py tests/test_profile.py tests/test_coordinator.py -q` — **101 passed in 0.70s**.
- `uv run ruff check .` — passed after wrapping one new overlong line.
- `uv run pytest` — **433 passed, 91 deselected in 9.93s**.
- `node --check assistant/snapshot.js`, `node --check assistant/static/app.js`, and `git diff --check` — passed.
- `uv run pytest -m browser` — **91 passed, 433 deselected in 64.18s**, on the final implementation.

No known failing checks. No paid APIs, browser shortcuts, site-specific policy, or live-reliability claims.
