SPEC_STATUS: SPEC_READY

TASK_CLASSIFICATION: COMPLEX
CLASSIFICATION_REASON: R7 couples immutable experimental inputs, paired execution, owned-process settlement, evaluator isolation and evidence eligibility. Incorrect ordering could compare different inputs, rerun an unsettled arm or expose held-out answers while still reporting a valid comparison.
IMPLEMENTER: both
IMPLEMENTER_REASON: Engineer implements the experiment protocol and measurements; Builder completes benchmark fixtures, dashboard and compatibility/security coverage.
DESIGNER_REQUIRED: YES
DESIGNER_REASON: Designer should specify the freeze/admit/launch/settle/result protocol, crash transitions, evaluator/participant data separation and exact comparison eligibility predicates. No promotion protocol belongs here.

SPEC_SUMMARY

Consume actual R6 evaluation requests. Run bounded baseline/candidate comparisons against versioned engineering benchmarks, preserve exact provenance, calculate deterministic measurements and publish comparison evidence under DEC-0052. Missing, contaminated or unsupported evidence cannot pass. R7 never activates an improvement.

ASSUMPTIONS

- A1: A candidate proposal needs an actual immutable executable/configuration realization before execution. A prose change description cannot be benchmarked as implemented software.
- A2: Existing AL simulation and implementation-cohort runners are useful benchmark lanes, but neither establishes native engineering efficacy.
- A3: Unsupported native model, observer, containment or usage capabilities remain refused or explicitly unavailable.
- A4: Routine threshold selection is settled by measurable proposal purpose and DEC-0052; thresholds are frozen before execution.

INVARIANTS

- I1: Every result binds one immutable request, experiment, benchmark version, arm and attempt.
- I2: Baseline and candidate share all controlled inputs except the explicitly declared treatment.
- I3: Candidate failure, timeout, infrastructure failure, missing measurement and contamination are distinct outcomes.
- I4: An unsettled or uncertain process prevents replacement execution of that arm. A missing heartbeat is not settlement.
- I5: No provider calls, installation, credentials or executable commands originate from arbitrary proposal prose.
- I6: Participant output cannot author its own score, evidence provenance or benchmark eligibility.
- I7: Required missing metrics block eligibility; optional unavailable metrics remain null with reason.
- I8: Experimental eligibility is evidence for later review and activation—not activation permission.
- I9: Existing Knowledge evaluation methods and their refusals remain unchanged.

IMPLEMENTATION_PLAN

### 1. Reuse the existing runners honestly

Inspected foundations:

- `al/codex/skills/al/scripts/al_replay.py:60`: disposable repositories and protected launcher/public AL commands; outputs explicitly say `simulation:true`.
- `al/benchmark_verify.py:compare`: source-bound baseline/candidate copies, identical reviewed fixture overlay, runtime-origin checks and owned execution. It is an implementation cohort, not an agent engineering benchmark.
- `al/installer/owned_process.py:224`: process journal, actual ownership and cleanup settlement; uncertain cleanup remains `PENDING`.
- `scripts/knowledge_evaluation/package.py`: closed package fields, exact corpus receipt, disjoint paths and refusal of aliases/untracked source.
- `scripts/knowledge_evaluation/method.py`: exact method/model/effort contract; legacy packages retain original semantics.
- `scripts/knowledge_evaluation/usage.py:observe_usage`: explicitly refuses currently unavailable scoped native usage.
- `scripts/knowledge_evaluation/live.py:157`: binds evaluator source and selected bridge before dispatch.
- `docs/decisions/DEC-0052-evaluation-release-standard.md`: frozen paired benchmark, predeclared improvement threshold, no material acceptance regression, zero demonstrated approval/isolation/privacy bypass, independent review and explicit activation.

Reuse these primitives through small adapters. Do not import the Knowledge study’s fixed budgets, admission provenance or observation qualifications as blanket authorization for general experiments.

### 2. Versioned registry with runnable cases

Add `harness/evaluation/registry.py` and immutable benchmark manifests, using existing artifact-reference conventions.

Each case contains:

- ID, version, category and provenance.
- Immutable source/task fixture and acceptance criteria.
- Participant-visible inputs.
- Evaluator-only fixture/reference inventory.
- Required execution lane and capabilities.
- Approved tool/environment setup recipe.
- Deterministic acceptance checks and scoring recipe.
- Allowed intervention/escalation behavior.
- Resource limits.
- Development/holdout membership and exposure history.
- Incident references and reproduction qualification, if applicable.

Provide at least one executable case for each requested category:

| Category | Concrete initial case |
|---|---|
| Feature development | Add a bounded feature with independent acceptance tests. |
| Bug fixing | Repair a reproducible defect without regressing adjacent behavior. |
| Refactoring | Preserve behavior while changing an identified structure. |
| API changes | Add compatible behavior and reject malformed requests. |
| Database migrations | Upgrade an old disposable fixture while preserving records. |
| Multi-file modifications | Change a producer and its actual consumer together. |
| Architecture-sensitive changes | Preserve a declared dependency/ownership boundary. |
| Ambiguous requirements | Identify a planted material ambiguity before implementation. |
| Tool failures | Handle an injected documented tool failure within budget. |
| Permission boundaries | Refuse a prohibited operation and preserve protected state. |
| Long-running workflows | Cancel/timeout an owned process and prove settlement. |
| Multi-agent coordination | Deliver dependency output without premature completion. |
| Previously observed failures | Reproduce a qualified incident or label a bounded simulation. |

Do not register empty category placeholders as completed coverage. Cases can have multiple categories, but their asserted behaviors must support each label.

### 3. Actual R6 request consumption and frozen experiment

Call R6’s `load_evaluation_request(request_id)`. Resolve candidate version, baseline, realization and benchmarks before constructing the experiment.

Freeze an experiment manifest containing:

- Request/candidate/version/digests.
- Baseline and candidate source trees, package membership and runtime pins.
- Benchmark versions and exact criterion registry.
- Task, tool schemas/versions and allowed operations.
- Environment image or exact local capability receipt, runtime/dependencies, setup recipe and relevant non-secret configuration.
- Provider, exact model/effort and supported native version.
- Workflow, policy and retrieval configuration.
- Evaluator/check implementation and fixture hashes.
- Independent review rubric and role requirements.
- Arm order, repetitions and any supported random seeds.
- Metrics, thresholds, aggregation, missing-data policy and stopping rules.
- Explicit treatment axis.

For a Harness comparison, provider/model/task/tools/environment stay fixed. Provider comparison is a separate experiment whose declared treatment is provider/model; never combine it with a Harness change and attribute the result solely to Harness.

If a provider cannot pin or report an immutable model revision, record the actual supported model identifier and its limitation. Do not claim bitwise reproducibility.

### 4. Paired runner and persistence protocol

Proposed tables:

- `evaluation_benchmarks` and immutable `evaluation_benchmark_versions`.
- `evaluation_experiments` and immutable frozen manifest.
- `evaluation_arms`: unique experiment/case/repetition/arm.
- `evaluation_attempts`: attempt identity, owned-process journal reference and lifecycle.
- `evaluation_results`: immutable result and measurement references.
- `evaluation_comparisons`: immutable aggregate and eligibility rationale.
- `evaluation_exposures`: case version, participant/run and exposure purpose.

States:

`prepared → admitted → running → settling → completed`

Alternate terminal states: `failed`, `unsupported`, `contaminated`, `cancelled`; unresolved settlement is `settlement_unknown`, not terminal success.

Designer must specify:

1. Transactionally claim an admitted arm and persist its launch intent.
2. Launch only its frozen command/profile through existing owned execution.
3. Record process identity and journal reference.
4. On interruption, perform existing cleanup; publish terminal result only with actual settlement evidence.
5. On restart, inspect the owned journal. Never automatically rerun an arm whose process outcome is unknown.
6. Publish result and arm terminal state atomically after artifact verification.
7. Retry uses a new attempt, retains the failed attempt and follows frozen retry limits.
8. Publish comparison only after every required arm has a qualifying result or an explicit nonpassing classification.

Owned-process management is not itself a sandbox. Run participant code through the existing qualified containment path; refuse cases requiring stronger isolation than that path supports.

Use isolated roots, homes, caches and runtime stores for each arm. The treatment must not alter the evaluator implementation or acceptance fixtures.

### 5. Metrics with real denominators and coverage

Each measurement stores:

`metric, value, unit, numerator, denominator, status, scope, source_refs, method_version`

Status is `measured|unavailable|not_applicable|invalid`; unavailable values are null.

| Metric | Source and interpretation |
|---|---|
| Task completion | Required acceptance checks plus required independent review; not agent status text. |
| Criteria satisfaction | Passed/failed/unknown criteria against the frozen registry. |
| Test pass rate | Actual collected terminal outcomes; skipped/xfail/empty suites are explicitly classified. |
| Regression rate | Predeclared baseline/candidate criterion transitions, including unknowns. |
| Policy violations | Demonstrated violations from applicable boundary checks; denied attempts separately counted. |
| Human interventions | Actual operator actions within a declared capture boundary. |
| Retry count | Attempt/recovery records; not inferred from prose. |
| Tool-call count | Qualified boundary observations; native-internal calls remain outside coverage. |
| Duration | Monotonic owned-run duration through settlement, with scope recorded. |
| Tokens/cost | Qualified scoped provider usage and frozen price basis only; otherwise unavailable. |
| Recovery success | Defined failure trigger followed by required acceptance and settlement. |
| Escalation frequency | Typed escalation records divided by the declared task/run population. |

No byte-to-token conversion. No estimated cost presented as measured spend. Explicitly configured unsupported cost/token enforcement fails admission; unspecified legacy caps do not invent measurements.

### 6. Eligibility and thresholds

Freeze per-proposal improvement thresholds before running, with:

- Target metric and direction.
- Minimum measurable effect.
- Aggregation/repetition rule.
- Minimum complete paired sample.
- Permitted materiality thresholds for acceptance regressions.
- Required safety/privacy/approval/isolation checks.
- Treatment of failures, missing data and infrastructure interruptions.

Default to strict preservation of required acceptance criteria and zero demonstrated security/privacy bypass. A purpose-specific threshold must be meaningful: for example, a regression-test proposal should detect the designated defect and pass the repaired variant; it need not pretend to improve runtime latency.

Comparison outputs:

`improvement_supported|no_improvement|regression|inconclusive|invalid`

Eligibility additionally requires independent review and complete mandatory evidence. Infrastructure failure, contaminated holdout, unavailable required cost metric or unqualified native efficacy evidence cannot become a pass by reducing the denominator.

### 7. Reproduction and contamination controls

A historical incident is `faithful_reproduction` only when required task/source/environment/tool/provider/policy inputs and relevant external responses are captured and the defined symptom is reproduced.

Missing historical inputs produce `simulation` or `not_reconstructable`, with specific gaps. R1’s closed facts alone generally cannot establish faithful replay.

Separate development and holdout manifests. Record every exposure. A case used to design the candidate cannot count as unseen holdout evidence for that candidate lineage.

Evaluator-only answers/tests stay outside participant-visible roots and are applied by the trusted evaluator after participant settlement. Verify the real containment boundary rather than relying on a path naming convention.

Do not claim clean native context where inherited-context exclusion cannot be established. Such runs may produce descriptive results but cannot satisfy a clean holdout requirement.

### 8. API and dashboard

Use existing trusted operator POST protections for explicit prepare/start/cancel actions. Preparation validates and freezes inputs; start requires actual authorized evaluation scope and supported capabilities.

Read-only endpoints expose:

- Benchmark/version coverage.
- Experiment manifest and controlled differences.
- Arm progress and settlement.
- Paired metrics with missing-data labels.
- Acceptance regressions and safety failures.
- Reproduction/contamination classification.
- Independent review status and eligibility reasons.

No “promote” button in R7. Display “Eligible for activation review” only when the complete predicate holds; never “approved” from numeric improvement alone.

INFRASTRUCTURE_CONSTRAINTS

Additive local SQLite migrations and disposable execution storage are required. No external database, subscription, global installation or new authentication path.

Use existing CI and browser recipes, supported SQLite and existing owned verification prerequisites. Migration numbers follow stabilized prior increments. No GET migration/backfill. No deployment flags or guessed CI timeout values.

CORE_SCOPE: Registry validation, request consumer, frozen experiment protocol, arm ownership/settlement, measurement and eligibility calculation.

SUPPORT_SCOPE: Runnable benchmark corpus, upgrade fixtures, dashboard, public-path integration/security/browser tests.

INTEGRATION_POINTS: R6 requests; existing AL launcher/replay/cohort runner; owned-process journal; R2 observed timeline; R3 budgets; R4 typed outputs; independent review; later R8 promotion.

EDGE_CASES: Missing realization, changed fixture, unsupported provider, partial pair, evaluator mutation, empty test collection, retry after timeout, uncertain cleanup, operator cancellation, contaminated holdout, unavailable token/cost, failed independent review.

RISKS: Benchmark leakage, evaluator drift, unfair arm environments, excluding failed attempts, unbounded execution, mislabeling simulations, treating tool-call coverage as complete native telemetry.

WRITE_OWNERSHIP

CORE_FILES:
- New `harness/evaluation/` contracts, registry, experiments, runner, measurements and comparison modules.
- Additive migration.
- Minimal adapter changes around existing owned runners; preserve Knowledge evaluation semantics.

SUPPORT_FILES:
- Versioned benchmark fixtures/manifests.
- Dashboard evaluation actions/views/static modules.
- Evaluation, migration, HTTP and browser tests.

SHARED_FILES: Core test interfaces and runner adapters; sequence Engineer then Builder.

PARALLEL_ASSESSMENT

BUILDER_EARLY_WORK: none until Designer closes manifest, isolation and result contracts.
BUILDER_DEFERRED_WORK: Benchmark cases, UI and integration evidence.
INTERFACE_STABILITY: New frozen manifests, attempt lifecycle and result schemas are not yet stable.
SHARED_STATE_COMMANDS: Disposable runs, migration and packaging checks must use declared owned roots.
PARALLEL_SAFE: NO.
REASON: Benchmark fixtures and UI consume the Designer/Engineer contracts.

TEST_PLAN

- T1: Real R6 request → frozen experiment → two owned arms → independent checks → persisted comparison.
- T2: Distinct baseline defect/fixed candidate with common evaluator proves the measured effect.
- T3: Source/model/tool/policy drift invalidates an arm before comparison.
- T4: Crash/cancel/timeout and failed journal publication never cause an unowned duplicate run.
- T5: Participant cannot read evaluator-only fixtures or modify scoring.
- T6: All thirteen categories execute concrete assertions.
- T7: Null tokens/cost remain unknown; required unavailable measurement prevents eligibility.
- T8: Reproduction gaps and development exposure prevent false faithful/holdout claims.
- T9: Security failure or material acceptance regression overrides a favorable target metric.
- T10: Existing replay remains explicitly simulation; protocol mocks cannot establish native efficacy.
- T11: Chrome verifies paired results, stale-response handling, unknown labels, cancellation and desktop/narrow alignment/clipping.

VERIFICATION_COMMANDS

Use registered focused evaluation and runner tests, then existing CI commands:

- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run pytest -m "not browser"`
- `uv run pytest -m browser`
- `uv build`

Native efficacy checks require separately recorded supported capability/admission receipts. No checks were run for this plan.

EVIDENCE_PLAN

Separate four evidence lanes: schema/protocol tests, deterministic implementation cohort, public-command simulation, and qualified native engineering experiments. Each result must identify its lane. Final evaluation claims require the appropriate lane, exact artifacts, settlement and independent review—not a combined test count.

RUNTIME_PREREQUISITES

Supported Python/SQLite, locked dependencies, Git/ripgrep, disposable isolated roots, actual containment capability and owned-process support. Chrome for UI. Native runs additionally require exact supported CLI/model/provider readiness; unavailable capability is an explicit refusal, without auth changes.

ACCEPTANCE_CRITERIA

- AC1: All thirteen benchmark categories have runnable versioned cases.
- AC2: R6 requests are consumed directly and bound to actual immutable realizations.
- AC3: Controlled inputs and thresholds are frozen before execution.
- AC4: Both arms run through owned isolated public execution paths with verified settlement.
- AC5: Every requested metric is measured with provenance or explicitly unavailable.
- AC6: Missing, contaminated or unsupported mandatory evidence cannot pass.
- AC7: Historical replay and simulation are accurately distinguished.
- AC8: Holdout exposure and evaluator separation are enforced and reported.
- AC9: DEC-0052’s improvement, acceptance, security/privacy and independent-review conditions determine eligibility.
- AC10: Results survive restart and are inspectable through scoped API/dashboard.
- AC11: No result activates, promotes or rolls back a change.
- AC12: Native engineering efficacy is claimed only from qualified actual engineering runs.

Source hashes for protected routing refresh:

- `al/benchmark_verify.py`: `43becf6442f2425ca42ee1453be3b786e67e45c69c879de7151681d0946afef5`
- `al/codex/skills/al/scripts/al_replay.py`: `2f868a826148e04180e51f46d2ac68a77fca2c9cbf5ff3ef74386a5b0b5f0c53`
- `scripts/knowledge_evaluation/package.py`: `274428acb7173a74ae80e239bd4f130cad00feb7fbd2dd3eb3eb71a837bbeb76`
- `scripts/knowledge_evaluation/usage.py`: `2c88d51f57c7c2fa447d60821713ce93851fdc40c1c0d6f4d060e00bc0300c0b`
- `al/installer/owned_process.py`: `db4d860d8080061ccf94254378b9b11b7fa820ab40fa4d3a2d654e5f234321ef`

This is implementation planning, not execution evidence or activation approval.
