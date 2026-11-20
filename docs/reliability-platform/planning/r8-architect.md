SPEC_STATUS: SPEC_READY

TASK_CLASSIFICATION: COMPLEX
CLASSIFICATION_REASON: Activation couples exact human approval, evaluated immutable configuration, concurrent execution reservation, bounded canary exposure and regression-triggered rollback. A race could otherwise apply an unapproved version, exceed exposure or roll back a newer deployment.
IMPLEMENTER: both
IMPLEMENTER_REASON: Engineer implements the activation/selection/rollback protocol; Builder completes migrations, dashboard and integration/security/browser coverage.
DESIGNER_REQUIRED: YES
DESIGNER_REASON: Specify linearization points, immutable approval bindings, selector CAS, exposure reservations, launch races, crash recovery and rollback precedence before implementation.

SPEC_SUMMARY

Turn an evaluated R6 candidate into an explicitly human-approved, versioned improvement applied only to eligible future executions. Support bounded canaries, measured stop conditions and safe rollback within the exact human-approved strategy. Preserve active executions, old runtime pins and all existing authority boundaries.

DEC-0049 requires explicit developer approval for every activation initially. DEC-0052 additionally requires frozen evaluation thresholds, no material acceptance regression, zero demonstrated approval/isolation/privacy bypass and independent review.

ASSUMPTIONS

- A1: R6 candidates and R7 results remain future interfaces until their implementations land. Revalidate their actual consumers before R8 coding.
- A2: The first executable activation type is a **bounded context-retrieval configuration** using existing `QueryLimits`; other types remain proposal/evaluation-capable but activation-refused until their application adapters are qualified.
- A3: Canary-to-general promotion requires a new explicit human approval. Successful canary evidence alone cannot widen exposure.
- A4: Automatic rollback means stop selecting the candidate for future executions and restore the exact approved baseline selector. It does not undo external side effects or patch running processes.

INVARIANTS

- I1: No agent can approve, activate, widen or rewrite its own authority.
- I2: Approval binds exact candidate, realization, evaluation, independent review, scope, baseline and rollout/rollback strategy.
- I3: Existing task/specification approval remains required for each execution; improvement approval does not replace it.
- I4: Every selected execution retains an immutable applied-configuration receipt.
- I5: Canary exposure is reserved atomically and bounded across concurrent starts.
- I6: Unknown measurement/settlement cannot establish canary success.
- I7: Rollback never overwrites a different, newer selector generation.
- I8: No mutation of active-run configuration, old pins or historical approval bytes.
- I9: Unsupported activation types fail before selection or application.

IMPLEMENTATION_PLAN

### Existing source boundaries

- `harness/approval.py` verifies sealed specification and current review/state bindings.
- `harness/dashboard/approve.py` uses that same ApprovalGate with stale-page guards; it does not trust an “approved” checkbox.
- `harness/decision_workflow.py` and `registry.py` provide actual developer verdicts and versioned DEC history.
- `harness/execution.py:start_execution()` validates approval and reserves an execution inside a transaction before launching.
- `harness/execution_handoff.py` provides immutable durable publication.
- `harness/execution_bootstrap.py:authority_snapshot()` pins trusted implementation bytes outside the writable repository.
- Existing `execution_runtime_activation.py` handles explicit runtime recovery/selection. It is not a general improvement rollout mechanism and should not be repurposed.
- R3’s moving `policy/configuration.py` consumes exact developer-authorized documents with revision checks. Do not call its execution-scoped installation operation as a shortcut for R8 rollout.

The R7 plan and Designer pseudocode bind comparisons to immutable realizations and independently validated eligibility. They explicitly withhold promotion authority.

### 1. First real application adapter

Implement `retrieval-limits-v1`:

- Closed fields: `bytes`, `assertions`, `hops`, `examined_edges`.
- Validate through existing `knowledge.models.QueryLimits.validate()`.
- No changes to source visibility, permitted source versions, review scope, query purpose, provider, filesystem or operation policy.
- No arbitrary query code, executable commands, prompt text or source paths.

Apply the selected configuration to actual assignment-context retrieval in `knowledge/integration.py:assignment_context()`. Preserve ordinary operator Knowledge queries unless separately included in the approved scope.

Qualification must demonstrate both actual retrieval behavior and unchanged query authority. A candidate with fewer returned bytes is not automatically better; R7 must measure its declared benefit and required acceptance preservation.

All ten R6 proposal types remain supported. Activation API returns `unsupported_application_adapter` for unqualified validators, tests, permission changes, contract changes, instructions, routing, sensors, checklists and escalation changes. Do not “apply” them by writing arbitrary files.

### 2. Immutable release manifest

Add `harness/improvements/releases.py` and an application-adapter registry.

Prepare a release manifest containing:

- Candidate ID/version/digest and exact realization.
- Application adapter ID/version and qualification references.
- Evaluation request/experiment/comparison references.
- Independent review reference and eligibility receipt.
- Exact baseline release/configuration.
- Repository/workflow/role applicability.
- Required runtime capability/profile.
- Canary maximum assigned executions and maximum unsettled exposures.
- Canary time window and minimum measured outcomes.
- Frozen success, regression and missing-data rules.
- Stop/rollback strategy, target baseline and treatment of reserved/running executions.
- Unsupported effects and limitations.

Preparation is read-only with respect to execution selection. It publishes an immutable preview; it does not activate.

### 3. Real human approval

Use the existing Harness decision/specification workflow to approve a concrete activation task whose sealed specification includes the release-manifest digest and action: `activate_canary` or `promote_general`.

At activation:

1. Resolve the actual ApprovalGate record for that task.
2. Verify sealed bytes, current review/state and repository binding.
3. Resolve the actual recorded decision/version required by the approved activation package.
4. Verify the manifest digest is the one approved.
5. Revalidate candidate/evaluation/review eligibility and applicable revocations.
6. Verify the expected selector generation.
7. Commit activation and selector change atomically.

The request supplies identifiers and expected bindings—not `approved:true`, arbitrary approval JSON or agent-authored approval text.

The developer action is attributed through the existing local operator boundary. Do not invent named-user authentication.

A changed candidate, baseline, scope, threshold or rollout strategy requires a new immutable release and actual approval.

### 4. Persistence and selector protocol

Proposed additive tables:

- `improvement_releases`: immutable manifest and artifact references.
- `improvement_activation_receipts`: release/action/approval/decision bindings and operator request ID.
- `improvement_selectors`: repository/application/scope key, selected release, baseline, generation and state.
- `improvement_exposures`: unique execution/application binding, selected release, selector generation and reservation state.
- `improvement_rollout_events`: immutable activation, stop, promotion and rollback observations.
- `improvement_outcomes`: exact exposure→measured result references.
- `improvement_action_requests`: idempotent request/result binding.

Scope matching must be deterministic and reject ambiguous overlapping selectors for the same application adapter. Initial support can use exact repository/workflow/role matches, with no implicit wildcard precedence.

Action replay:

- Same request ID and exact request → original result.
- Same ID, different request → conflict.
- Wrong expected generation → stale-selection conflict.
- Immutable artifact failure → no selector change.

### 5. Future execution application

Integrate selection into the **new execution branch** of `start_execution()`.

Designer must establish ordering around:

1. Existing task approval and launch-readiness checks.
2. Selection of a compatible active release.
3. Atomic canary-slot reservation with execution reservation.
4. Durable immutable configuration receipt in the execution envelope.
5. Trusted worker verification before use.

Preserve original task approval fields. Add an explicit separate improvement-selection binding rather than rewriting the meaning of that approval.

Duplicate execution reservation reuses the original selection. Resume/reconnect never rereads the current project selector to change behavior.

If an old pinned runtime cannot consume the new receipt, it remains on its existing behavior; it must not be counted as candidate exposure or silently upgraded. For an explicitly candidate-required run, refuse incompatibility instead of falling back invisibly.

No SQLite transaction waits for native launch or completion.

### 6. Bounded canary and stop behavior

Count **assigned executions** conservatively. Reserving a canary slot consumes exposure even if subsequent launch is uncertain. Do not refund on timeout, missing heartbeat or unknown startup outcome.

When the cap, unsettled limit, time window or a stop condition is reached, no further candidate selections occur.

Collect outcomes from existing R2/R7 and authoritative acceptance/security records. Required unavailable outcomes keep the canary inconclusive; they cannot be discarded from the denominator.

Automatic monitor action is allowed only by the previously approved release strategy:

- Append the triggering evidence.
- CAS the selector from that exact candidate/generation.
- Stop new candidate assignments.
- Restore the approved baseline if its qualification remains valid.
- Otherwise enter `stopped_no_compatible_baseline`, requiring no candidate continuation.

A rollback must not overwrite a later human-selected release. Record a superseded/no-op result when its expected generation no longer matches.

Already running executions retain their immutable configuration. Reserved-but-not-started candidate executions are blocked from launch after stop unless the approved strategy explicitly permits them. Do not silently replace their candidate configuration with baseline bytes.

### 7. Promotion and rollback semantics

Canary success creates `ready_for_promotion_review`, not general activation.

General promotion requires a new exact approval binding to:

- Successful canary evidence.
- Same candidate/realization.
- Proposed wider scope and limits.
- Current baseline and selector generation.
- Updated rollback strategy where applicable.

Rollback preserves release lineage and all exposure/outcome history. It cannot claim that files, database changes, provider actions or other external effects were reversed. The first retrieval-only adapter avoids such external application effects, but ongoing execution behavior remains unchanged.

### 8. Operator dashboard

Extend the R6 Improvement Center:

- Exact release preview and configuration diff.
- Approval/decision/evaluation lineage.
- Adapter qualification and unsupported-type reasons.
- Canary cap, assigned/unsettled/measured counts.
- Scope and selector generation.
- Stop reasons and missing evidence.
- Explicit approve/activate, stop, promotion-review and rollback actions.
- Per-execution applied-version links.
- Distinct “approved”, “selected”, “applied”, “measured” and “rolled back for future executions” states.

Use existing Host/Origin/body limits, stale-preview guards and request IDs. GET never activates or reconciles selectors.

INFRASTRUCTURE_CONSTRAINTS

Additive local SQLite migrations and immutable private artifacts only. Startup is schema-only; no scan/backfill of historical executions.

No provider installation, service, global configuration rewrite, subscription or deployment platform. Existing CI uses locked dependencies and existing browser setup; no timeout/flag values are inferred.

CORE_SCOPE: Approval consumption, release manifests, selectors, canary reservation, execution binding/application, outcomes and rollback CAS.

SUPPORT_SCOPE: Dashboard, migration compatibility, qualified retrieval fixtures and integration/security/browser tests.

INTEGRATION_POINTS: ApprovalGate/DecisionWorkflow; R6 candidate consumer; R7 comparison/review consumer; execution reservation/handoff; Knowledge QueryLimits; R2 outcomes; stabilized R3 compatibility checks.

EDGE_CASES: Concurrent starts, cap exhaustion, duplicate activation, stale approval, withdrawn evaluation, incompatible baseline, crash after artifact publication, stop/start race, uncertain launch, newer selector during rollback, unsupported adapter, resumed old execution.

RISKS: Approval laundering, overexposure, selection/application mismatch, rollback of a newer release, confusing canary success with promotion and claiming reversal of external effects.

WRITE_OWNERSHIP

CORE_FILES:
- New `harness/improvements/releases.py`, `activation.py`, `selection.py`, `rollout.py`, application adapter modules.
- Additive migration.
- Minimal `execution.py`, handoff validation and trusted snapshot closure changes.
- `knowledge/integration.py` retrieval-limit application.
- Core action/API and correctness-sensitive client state.

SUPPORT_FILES:
- Improvement Center styling/views.
- Activation/canary/rollback/migration/browser tests.
- Documentation and fixture qualification evidence.

SHARED_FILES: Core tests and contracts; Engineer then Builder.

PARALLEL_ASSESSMENT

BUILDER_EARLY_WORK: none before Designer closes the protocol.
BUILDER_DEFERRED_WORK: UI, fixtures and integration coverage.
INTERFACE_STABILITY: Release/approval/selection/exposure contracts are new.
SHARED_STATE_COMMANDS: Migrations and broad verification sequenced.
PARALLEL_SAFE: NO.
REASON: All support paths consume the coupled activation protocol.

TEST_PLAN

- T1: Actual approved candidate/evaluation → real sealed activation approval → canary → new execution receives and uses exact retrieval limits.
- T2: Forged approval, stale review, changed candidate or ineligible evaluation cannot select a release.
- T3: Concurrent reservations never exceed exposure/unsettled limits.
- T4: Crash/replay at each publication boundary produces no duplicate activation or inconsistent execution selection.
- T5: Regression stops new exposures and restores only the approved baseline through exact-generation CAS.
- T6: Rollback cannot overwrite a newer release or alter an active/resumed execution.
- T7: Missing outcomes remain inconclusive; promotion requires a separate real human approval.
- T8: Unsupported proposal types remain visible but cannot apply arbitrary files/configuration.
- T9: Retrieval changes affect actual bundles while preserving source visibility, review restrictions and execution authority.
- T10: Chrome verifies preview drift, action replay, canary counts, lineage and desktop/narrow visual behavior.

VERIFICATION_COMMANDS

Register focused activation/selection/rollback/Knowledge integration suites, then existing Ruff, non-browser pytest, browser pytest and build recipes. No tests were run for this plan.

EVIDENCE_PLAN

Require actual ApprovalGate and producer-consumer integration, not mocked booleans or fabricated receipts. Exercise concurrent starts and crash windows with deterministic tests; verify real retrieval output changes and preserved authority. R7 efficacy evidence remains independently required.

RUNTIME_PREREQUISITES

Supported Python/SQLite, locked environment, disposable local state, owned execution fixtures and Chrome. No new authentication, native observer or external database prerequisite for the initial retrieval adapter.

ACCEPTANCE_CRITERIA

- R8-01: Activation requires exact real human approval plus qualified evaluation and independent review.
- R8-02: The initial retrieval adapter changes actual future-execution behavior.
- R8-03: All unsupported types fail safely without narrowing R6’s proposal coverage.
- R8-04: Canary exposure is durably bounded under concurrency and interruption.
- R8-05: Every execution retains exact applied-version provenance.
- R8-06: Regression stop/rollback follows the approved strategy and cannot overwrite newer selection.
- R8-07: Active executions and old pins remain unchanged.
- R8-08: Promotion requires a new explicit approval.
- R8-09: Unknown outcomes never establish success.
- R8-10: Dashboard reflects actual approval, selection, exposure and rollback states.
- R8-11: No agent-facing self-activation or authority-changing path exists.

Designer must revalidate moving R3 interfaces and the implemented R6/R7 consumers. Inspection hashes:

- `harness/approval.py`: `85e78dca804372ff12dcb621b164f06b0c356edf54a99e3c0e82e76b2f275261`
- `harness/dashboard/approve.py`: `ef0e243b62a58e1da4427b29123c602ede414d90e1a3a888d4561de7ff582ae2`
- `harness/execution.py`: `fbae456b0c81e9b4e28f8f3b07d24675edcb7320c1ab1d26f80e93f2aa2899d0`
- `harness/execution_bootstrap.py`: `657c10361526760536ef8a613bb0beeaaf531e576192083abed8920a037a7655`

This is a source-backed implementation plan, not activation approval or passing verification.