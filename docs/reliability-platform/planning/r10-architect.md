SPEC_STATUS: SPEC_READY

TASK_CLASSIFICATION: COMPLEX
CLASSIFICATION_REASON: Recovery must bind diagnosis, new evidence, existing AL authorization, R3 budget reservation and actual process settlement to one durable attempt. Incorrect crash or concurrency handling could replay an effect, spend authorization twice or continue from an obsolete checkpoint.
IMPLEMENTER: both
IMPLEMENTER_REASON: Engineer implements recovery selection/admission/lifecycle and stagnation detection; Builder completes compatibility, failure-injection and dashboard coverage.
DESIGNER_REQUIRED: YES
DESIGNER_REASON: Specify the recovery-attempt protocol, effect-safety classification, checkpoint/reason bindings, budget consumption and cancellation/restart races. Reuse existing caps and settlement mechanisms rather than replacing them.

SPEC_SUMMARY

Extend the existing AL lifecycle with evidence-driven, bounded recovery. Detect repeated failures and unproductive edit/check cycles; require a concrete reason to expect improvement before another attempt. Persist diagnosis, action and outcome across interruption. Preserve separate AL rework/review caps, explicit continuation authorization, approved specification and actual external-effect uncertainty.

ASSUMPTIONS

- A1: Recovery selection may be automatic within already authorized scope; it cannot manufacture authorization.
- A2: A registered check is not inherently pure. Automatic rerun requires a qualified effect profile.
- A3: R3 budget APIs are moving and must be revalidated before integration.
- A4: Missing tools/provider readiness are operational issues. Only material specification or engineering choices become developer blockers.

INVARIANTS

- I1: Existing AL rework, review-correction, recovery-reassessment and continuation budgets remain separate and authoritative.
- I2: No retry resets a cap, changes acceptance criteria or silently edits approved architecture.
- I3: Repeated identical action requires supported new evidence or a changed relevant input—not merely new wording.
- I4: Unknown external effects or uncertain process settlement prohibit automatic replay.
- I5: Reconnect observes; resume continues the stored invocation under current authority. Neither means “start again.”
- I6: Cancellation acknowledgment is not settlement.
- I7: Recovery results retain failed attempts and verification obligations.
- I8: Native accounting gaps remain explicit; configured unsupported enforcement fails closed.

IMPLEMENTATION_PLAN

### Existing mechanisms to retain

- `al_rework.py` already requires a recorded failing repro for a demonstrated cause, routes nonlocal/contract/safety/specification issues to Architect, and starts with two return credits.
- `al_rework_recovery.py` provides durable reassessment packets and evidence-bound grants. Repeating the same approach without new evidence is refused.
- `cmd_continue()` binds explicit continuation to the latest stopping checkpoint and current candidate; independent blockers cannot be cleared by a budget continuation.
- `al_evidence.py` records actual check runs and before/after scope fingerprints.
- `harness/execution_recovery.py` distinguishes reconnect, resume and reconciliation; uncertain native identity/process outcome prevents retry.
- `execution_recovery_settlement.py` checks native, browser and delivery settlement.
- `execution_controls.py` performs explicit recovery through existing bridge and resume paths. Do not automatically invoke it as a generic retry shortcut: it may involve bridge recovery.
- R1 supplies dispatch observations; R2 supplies owned-process lifecycle and evidence links. Neither supplies complete external-effect knowledge.

### 1. Closed recovery diagnosis

Add `harness/recovery/contracts.py` and corresponding AL metadata adapters.

A diagnosis binds:

- Execution/run/task/epoch and actual phase occurrence.
- Current candidate/specification/requirement bindings.
- Latest checkpoint or exact AL recovery occurrence.
- Source failure IDs and valid evidence references.
- Failure category and provenance.
- Attempt history and observed effect state.
- Applicable existing cap/authorization references.
- Proposed strategy and reason.
- New evidence or changed inputs.
- Remaining verification obligations.

Categories include deterministic defect, invalid contract, stale input, unavailable prerequisite, transient qualified provider/tool failure, budget exhaustion, cancellation, stagnation and unknown effect.

Do not classify arbitrary stderr as a known transient failure.

### 2. Strategy registry with real actions

Implement a closed strategy registry:

| Strategy | Required predicate | Actual consumer |
|---|---|---|
| Observe/reconnect | No new work requested | Existing reconnect/status path. |
| Rerun qualified check | Settled prior run, safe effect profile, valid reason/new evidence, remaining budgets | Existing registered `evidence run` route. |
| Local defect rework | Recorded repro, existing fast-path criteria satisfied | Existing `rework` command; it consumes its own credit. |
| Architect reassessment | Existing route requires a decision or batch is exhausted | Existing packet/attempt/decision protocol. |
| Refresh permitted context | Specific missing/stale context evidence | Existing Knowledge query/refresh authorization; no scope expansion. |
| Repair prerequisite | Explicit bounded operational repair action already authorized | Existing registered repair/check path; no generated shell command. |
| Resume invocation | Known identity, valid current authority and actual settlement | Existing resume path. |
| Escalate material choice | Actual unresolved specification/engineering decision | Existing typed blocker workflow. |
| Stop terminally | No authorized useful strategy, cap reached or unsafe uncertainty | Persist terminal report/checkpoint; preserve obligations. |

Provider fallback remains the existing fixed model-slot policy. Recovery cannot invent a substitute model or reduce effort to escape a failure.

### 3. Repeated failure and stagnation detection

Create deterministic signatures from safe structured inputs:

- Strategy/action ID and registered command/profile identity.
- Criterion/check IDs.
- Candidate/scope fingerprint.
- Policy/specification revision.
- Closed failure code and effect classification.
- Relevant environment/capability receipt.

Do not hash raw secrets, arbitrary arguments or full diagnostics.

An identical signature plus unchanged relevant evidence is a repeated attempt. A free-text “try again” reason does not qualify.

Track stagnation over a bounded window of actual edit/check cycles:

- Source/candidate fingerprints and changed-file identities.
- Same check/criterion population and evaluator recipe.
- Passed/failed/unknown criterion sets.
- Repro outcome and regression set.
- Actual recovery strategy and new-evidence references.

Default detection can flag three comparable cycles with no acceptance improvement, including oscillation back to a prior candidate. This is a diagnostic threshold, not a replacement for existing caps.

Different test populations or stale/missing evidence yield `not_comparable`, not improvement. A new file hash alone does not prove progress.

On stagnation, stop repeating the affected strategy and select diagnosis/reassessment. Independent authorized tasks remain eligible.

### 4. Evidence-backed repeat reasons

A repeat request must use a closed basis:

- `relevant_source_changed`
- `prerequisite_repaired`
- `new_independent_evidence`
- `qualified_transient_condition_cleared`
- `revised_diagnosis`
- `explicit_bound_continuation`

Resolve supporting references and verify they are newer/relevant to the prior attempt. Explicit continuation can grant only what the existing continuation protocol permits; it does not settle unknown effects or waive approval.

### 5. Persistent attempt protocol

Proposed recovery records:

- Immutable diagnosis/version.
- Recovery plan with selected strategy and prerequisite bindings.
- Attempt with unique request ID, action binding, state and ownership.
- Budget/authorization references.
- Outcome and terminal/checkpoint report.
- Stagnation observations.

Designer specifies:

1. Validate current source, epoch, candidate, checkpoint and strategy.
2. Validate existing AL authorization without consuming it twice.
3. Reserve R3 retry/cost/time resources through its actual admission API.
4. Persist unique action intent before dispatch.
5. Dispatch only through the existing guarded public action.
6. Record actual invocation/process/ledger identity.
7. Settle the action through its owning subsystem.
8. Publish outcome and updated recovery state.
9. Reassess remaining obligations without erasing failures.

Do not hold SQLite while waiting for a process or native response.

The AL state remains authoritative for AL return credits and review phases. Harness records references/projections; it must not maintain a competing credit counter.

Crash after action intent but before result is uncertain until the existing action receipt proves what happened. No automatic replacement solely because a worker disappeared.

### 6. Effect-safety classes

- `pure_read`: qualified read-only action with no relevant external side effects.
- `disposable_check`: writes confined to owned disposable roots; process settlement required.
- `idempotent_external`: only where the actual provider supports a verified idempotency key and replay contract.
- `compensatable`: explicit approved recovery procedure, not assumed rollback.
- `unknown_or_nonrepeatable`: no automatic replay.

Tests may contact services or run migrations; never infer safety from “pytest” or a check label.

For unknown effects, provide reconciliation requirements and retain the attempt. Human prose alone must not become stronger proof than the existing settlement contract allows.

### 7. Cancellation and restart

Persist cancellation intent under current authority. Prevent unlaunched attempts from dispatching after cancellation wins the admission race.

For launched work, invoke its existing cancellation/cleanup owner. Keep `cancelling` or `settlement_unknown` until positive closure evidence exists.

Restart loads retained recovery state and exact predecessor receipts. It does not reset retries, rework credits, deadlines, stagnation history or latest checkpoint binding.

Terminal report includes:

- Trigger and attempted strategies.
- Exact failed/current evidence.
- Remaining criteria.
- Budget/cap disposition.
- Effect/settlement uncertainty.
- Escalation or continuation requirements.
- Whether independent work remains eligible.

### 8. Dashboard and incident integration

Extend Execution Explorer with strategy, reason/new evidence, attempt timeline, budget disposition, stagnation state and settlement.

Use explicit controls for existing resume/reconciliation/continuation actions. A generic “Retry” button must not bypass the strategy-specific requirements.

Publish closed recovery observations to R2 and R5 through their qualified source adapters. Do not inject raw AL state or diagnostics.

INFRASTRUCTURE_CONSTRAINTS

Additive local persistence and existing AL state evolution only. No new service, background retry daemon, external database or provider credential path. Existing runtime pins require compatibility handling; no silent upgrade.

CORE_SCOPE: Diagnosis, strategy predicates, stagnation, durable attempt admission and existing-action integration.

SUPPORT_SCOPE: Dashboard, fixtures, state compatibility and failure-injection/security/browser tests.

INTEGRATION_POINTS: AL rework/recovery/evidence/checkpoints; Harness epochs/settlement/resume; R1/R2 facts; stabilized R3 budgets; R5 incidents; R9 context.

EDGE_CASES: Repeated identical failure, oscillating files, changed test denominator, stale continuation, exhausted separate caps, missing native identity, uncertain side effects, cancellation/launch race, source drift, unavailable usage and obsolete callback.

RISKS: Double spending authorization, duplicate external work, false progress, blanket task stoppage, weakened settlement and generic retries bypassing existing AL governance.

WRITE_OWNERSHIP

CORE_FILES:
- New `harness/recovery/` contracts, diagnosis, strategies, attempts and stagnation modules.
- Minimal existing recovery/settlement/action integration.
- Both AL platforms: bounded recovery metadata/action adapters around existing rework/evidence/checkpoint APIs.
- Additive migration/state compatibility validation.

SUPPORT_FILES:
- Dashboard recovery views/styles.
- Recovery protocol, stagnation, compatibility, public-action and browser tests.
- Documentation and exact effect-profile fixtures.

SHARED_FILES: Core contracts and focused tests; sequential ownership.

PARALLEL_ASSESSMENT

BUILDER_EARLY_WORK: none.
BUILDER_DEFERRED_WORK: UI and integration coverage after Designer/Engineer contracts stabilize.
INTERFACE_STABILITY: Diagnosis/attempt/effect contracts are new; R3 mappings require later inspection.
SHARED_STATE_COMMANDS: Migration, AL packaging/parity and broad verification sequenced.
PARALLEL_SAFE: NO.
REASON: Support work consumes the recovery protocol.

TEST_PLAN

- T1: Settled safe check failure plus verified repair → guarded rerun → actual ledger outcome.
- T2: Identical failed action without new evidence is refused without spending another attempt.
- T3: Comparable edit/check cycles and candidate oscillation trigger stagnation; changed denominators do not fabricate progress.
- T4: Existing two-return/reassessment/continuation rules remain unchanged, including latest-checkpoint binding.
- T5: Crash at each intent/dispatch/result boundary never automatically duplicates uncertain work.
- T6: External-side-effect/unknown-settlement actions cannot use pure-check retry.
- T7: Concurrent attempts and cancellation respect R3 reservations and current epoch.
- T8: Explicit unsupported cost/token enforcement refuses; unknown usage is never zero.
- T9: Operational failures produce issues; actual material choices produce typed blockers; independent work remains eligible.
- T10: Restart preserves caps, deadlines, history and unresolved criteria.
- T11: Chrome verifies strategy-specific controls, stale responses, cancellation/settlement labels and desktop/narrow layout.

VERIFICATION_COMMANDS

Register focused recovery/rework/settlement suites, both-platform parity and dashboard tests; retain existing Ruff, non-browser pytest, browser pytest and build recipes. No checks were run here.

EVIDENCE_PLAN

Drive real public AL/Harness actions, not direct state fabrication. Include current R3 budget contracts, process owners, continuation schemas, artifact readers and dashboard consumers. Native resume/effect reconciliation requires actual qualified boundary evidence; deterministic fixtures alone cannot establish provider behavior.

RUNTIME_PREREQUISITES

Supported locked Python/SQLite, disposable repositories/state and owned process fixtures. Native qualification additionally requires a supported CLI/profile/authentication state; unavailable capability remains an operational obligation.

ACCEPTANCE_CRITERIA

- R10-01: Failure-aware strategies invoke existing guarded actions.
- R10-02: Repetition requires relevant new evidence or valid bound authorization.
- R10-03: Stagnation detection uses comparable verification and edit history.
- R10-04: Existing independent AL caps and continuation rules remain authoritative.
- R10-05: Recovery budgets and process ownership survive interruption.
- R10-06: Unknown effects/settlement cannot trigger automatic replay.
- R10-07: Cancellation and restart preserve exact authority and history.
- R10-08: No silent specification, acceptance, model-policy or permission changes.
- R10-09: Escalation and terminal reports accurately retain unresolved work.
- R10-10: Dashboard and incidents expose actual recovery evidence.
- R10-11: Native limitations and unsupported accounting remain explicit.

Inspection bindings for later refresh:

- `al/.../al_rework.py`: `6a85af70e5a8242bdaea0cf9fdf7f13223c2f9b0879d4ecb539d4133a3130c7b`
- `al/.../al_rework_recovery.py`: `a1946d0629d0a01582d36dcca33f428ff1b731e1ddb871993cb542ef8942cc6b`
- `harness/execution_recovery.py`: `e4c37b55dce077b156068c255bf7175e133d8ea733933d13af34c0be937a1bb8`
- `harness/execution_recovery_settlement.py`: `5062ed689846c5e6eb5b17c7eee2ccac118eae814e909eea03f6edd2eaa349fe`

The AL hashes refer to the inspected Codex script paths. Revalidate both platform copies and actual predecessor consumers before implementation. No further planning or execution is started pending those concrete consumers.