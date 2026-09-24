# Task 10 implementation report

Implemented bounded progress/recovery and full-input field cache validity. Existing agent/core runtime is unchanged; these are the staged owners for Tasks 11/12.

## Behavior

- `track_progress` detects three non-WAIT actions without relevant change and repeated semantic state/action pairs, including LOOK and scrolling. WAIT does not erase stalled actions; it uses a monotonic 30-second deadline and never itself spends a decision budget. Cancelled, paused, waiting-user, blocked and terminal tasks cannot continue waiting/work through this API.
- `record_progress_change` registers current evidence for accepted input, authentication transitions, requested options, relevant content, or confirmed outcomes. Unregistered, stale, screenshot-only, and timer-only evidence cannot reset progress. Evidence identities deduplicate repeated quotes even when observations/citation IDs change.
- `begin_recovery` records distinct normalized semantic approaches for an objective/state. Changed reasons or question wording do not allocate a fresh approach. After two approaches, another stall blocks with the remaining objective. Recoveries and counts remain in state.progress, with diagnostics.
- `spend` reserves action/Jev/coordinator budget before work at 150/300/60. Exhaustion blocks with the remaining objective. Coordinator schema repair attempts share the same counter; the existing coordinator_calls alias remains compatible.
- Field cache keys include full helper input, task identity/revision, challenge identity/current record, constraints and all valid facts. Same-host/label fields with different form purpose, changed provider/address/constraints, or changed/expired challenge cannot reuse values. The process-only FieldCache permits equivalent stale-before-dispatch reuse and consumes/tombstones the entry before dispatch; the same key cannot be reinserted after an uncertain execution.

## RED / GREEN evidence

1. Added focused regression tests, ran `uv run pytest tests/test_state.py tests/test_interaction.py tests/test_coordinator.py -q`: **19 failed, 143 passed**. Missing progress/cache APIs caused 18 failures; the shared coordinator budget test failed because the existing separate counter allowed the repair request.
2. Implemented APIs and shared coordinator spending; focused run: **162 passed**. First Ruff run found E731 for a local lambda; replaced with a named function.
3. Added second-recovery-next-stall regression plus stale-clock/evidence and consumed-cache coverage. Focused run: **1 failed, 164 passed**. The second recovery still returned recover rather than blocked. Added active recovery tracking; focused **165 passed** and Ruff passed.
4. Added same-ID challenge expiry cache regression: **1 failed, 79 passed** in interaction tests. The cache reused an expired challenge reference. Included the current challenge record in the key; final focused run: **166 passed**.

## Final validation

- `uv run pytest tests/test_state.py tests/test_coordinator.py tests/test_interaction.py -q`: **166 passed**.
- `uv run ruff check .`: **passed**.
- `uv run pytest`: **687 passed, 93 deselected** (10.02s).
- `uv run pytest -m browser`: **93 passed, 686 deselected** (64.51s; collected before the final pure cache regression was added).
- `node --check assistant/snapshot.js`: **passed**.
- `node --check assistant/static/app.js`: **passed**.
- `git diff --check`: **passed**.
- No paid APIs; browser suite uses repository throwaway headless Chrome fixtures.

## Tasks 11/12 integration requirements

- Call `spend(state, 'jev')` immediately before each Jev request and `spend(state, 'actions')` immediately before actual dispatch. No browser/action retry is introduced here.
- While track_progress returns wait, schedule read-only observations using elapsed monotonic time; yield to inbound user/cancellation events. Do not request Jev on each polling iteration. This task tests cancellation at the pure state boundary; actual interruptible loop scheduling belongs in Task 12.
- Register semantic change evidence only after an owning acceptance/auth/outcome gate or validated semantic interpretation proves objective-relevant change. Raw coordinator summary/evidence citations do not themselves prove change. The new registry is not a model-call side effect.
- Pass a stable stalled objective and semantic state to begin_recovery; do not substitute question wording or a recovery's proposed new objective for the original stalled objective. A duplicate approach returns false without granting browser authority. Jev still chooses every operation and target.
- Keep one FieldCache per task; pass the complete sanitized helper payload. Resolve raw secrets only through SecretStore. After freshness checks, call consume BEFORE dispatch and retain it consumed for uncertain results. Retain the existing execution/outcome gate so a changed helper key cannot authorize an uncertain mutation retry. Clear cache on terminal cleanup; do not serialize it.
- A changed observation alone intentionally does not invalidate field values if the complete semantic helper input and task/challenge binding remain equivalent. Callers must remove volatile observation IDs from the helper request itself only if they are not helper inputs; this cache never silently drops payload fields.
