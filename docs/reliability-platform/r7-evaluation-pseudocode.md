PSEUDOCODE

## harness/evaluation/contracts.py, registry.py (CORE)
purpose: Define immutable runnable benchmarks and keep evidence lanes distinct.
signatures: New `validate_case(document) -> BenchmarkVersion`, `publish_case(conn, document, expected_head, request_id) -> BenchmarkVersion`; names are proposed additions.
inputs / outputs: Reviewed concrete fixtures, checks and capability requirements → immutable version and digest.
state changes: Append version; atomically compare/update head. Never rewrite historical versions.
control flow:
  1. Validate closed schema, bounded fields, canonical paths, exact artifact inventories, declared categories, criterion IDs and approved setup/command recipes.
  2. Require each category claim to reference executable assertions. An enum label alone does not establish coverage.
  3. Separate participant-visible source/task material from evaluator-only answers, checks and rubric. Bind both inventories without exposing hidden contents through public manifests.
  4. Declare lane: `protocol`, `implementation_cohort`, `public_command_simulation`, or `qualified_native_engineering`.
  5. Declare development/holdout membership, candidate lineage exposures and reproduction qualification.
  6. Validate deterministic check collection, permitted interventions, retries, budgets and expected failure injection.
data flow: Case references resolve immutable bytes, modes and complete membership, not mutable branch names.
failure / error behaviour: Refuse unsupported schemas, aliases, absent assertions, arbitrary commands derived from prose, or incomplete realization.
edge cases: Multiple categories are allowed only with distinct supporting assertions. Historical cases with reconstruction gaps remain simulation or not reconstructable.
integration points: Reuse bounded validation patterns from `scripts/knowledge_evaluation/package.py`; do not inherit its fixed `BUDGETS`, `PROVENANCE` or study-specific approval.

## harness/evaluation/experiments.py, migrations/<next_available>_evaluation.sql (CORE)
purpose: Freeze the complete paired experiment before admission.
signatures: New `prepare_experiment(conn, request_id, realization_ref, case_refs, expected_request_digest) -> Experiment`; future R6 dependency is named `load_evaluation_request`, whose module/signature must be revalidated after R6 lands.
inputs / outputs: Actual persisted R6 request plus immutable executable/configuration realization → frozen experiment.
state changes: Immutable manifest, required arm matrix, exposure records and experiment state `prepared`.
control flow:
  1. Call the real R6 consumer. Verify exact candidate version/digest and request binding; never reconstruct it from dashboard prose.
  2. Resolve baseline/candidate source inventories, runtime pins and explicit treatment axis.
  3. Freeze task fixtures, tools/schemas, permitted operations, provider/model/effort/native version, workflow/policy/retrieval configuration, dependency/setup receipts and environment capability receipt.
  4. Freeze evaluator implementation, check inventory, rubric/reviewer role, arm order, repetitions, supported seeds, thresholds, aggregation, stopping rules and missing-data policy.
  5. Compute controlled differences. Require every difference to be either:
       a. the declared treatment;
       b. a necessary derived artifact explicitly bound to that treatment; or
       c. a declared nuisance difference permitted by the method.
     Any unexplained difference invalidates admission.
  6. For a Harness comparison, provider/model/task/tools/environment must match. Provider treatment cannot also claim isolated Harness efficacy.
  7. Freeze all required `(case_version, repetition, arm)` rows with unique constraints. No result-dependent additions or deletions.
  8. Publish manifest artifacts durably outside participant authority; verify bytes, then transactionally insert manifest references and arms.
data flow: Immutable request → manifest digest → every arm/attempt/result/comparison.
failure / error behaviour: Mutable realization or unsupported required capability returns structured readiness refusal, without launch.
edge cases: Nonimmutable provider model identifiers retain explicit reproducibility limits. New thresholds or treatment require a new experiment.
integration points: Existing SQLite transaction pattern and immutable artifact publication. No GET migration/backfill. R6 implementation absence is a future integration obligation, not an invented existing API.

## harness/evaluation/admission.py, isolation.py (CORE)
purpose: Qualify authorization and evaluator separation before any participant exposure.
signatures: New `admit_experiment(conn, experiment_id, authorization_ref, capability_receipts)`, `verify_arm_boundary(frozen, arm_layout)`.
inputs / outputs: Frozen experiment and actual authorization/capability receipts → admitted experiment or unsupported result.
state changes: `prepared → admitted`; record admission provenance and exposure commitments.
control flow:
  1. Revalidate manifest/source membership and actual authorized evaluation scope. Neither candidate publication nor DEC-0052 is execution authorization.
  2. Select only supported public runners and qualified containment. Verify executable/runtime origin and effective policy support.
  3. Create separate arm repositories, homes, caches, runtime stores and output directories. Check canonical paths, symlinks, hardlinks and overlapping roots.
  4. Keep evaluator-only files outside participant-readable authority, including alternate tools, subprocess reads, imports, logs, retrieval indexes and inherited context.
  5. Verify the actual enforced boundary and its executable probes. Path separation alone is insufficient.
  6. Require unchanged evaluator bytes and common fixtures for both arms. Candidate treatment must not select or modify scoring.
  7. Record participant lineage exposure before delivering material. An uncertain delivery counts conservatively as possible exposure.
  8. Reject explicit token/cost enforcement without qualified scoped bounds and enforcement. Without such caps, keep usage unavailable.
data flow: Capability receipts describe established isolation; they never manufacture missing platform support.
failure / error behaviour:
  - Existing `prepare_isolation(...)` allows broad file reads with fixed exclusions; arbitrary hidden-test exclusion is not established by that function.
  - Existing Knowledge `EvaluatorAdapter` rejects active writers and permits a read-only study. Do not relabel it an engineering runner.
  - Unsupported native engineering or clean-holdout requirements refuse the affected lane.
edge cases: Fresh local directories do not prove absence of prior model/context exposure. Unknown inherited-context exclusion prevents clean-holdout eligibility.
integration points: Preserve `package.canonical_path/disjoint/corpus_receipt`, `method.validate_method`, selected-bridge verification, observer refusals and existing public admission guards. Reuse small adapters, not study authorization.

## harness/evaluation/runner.py; narrow al/installer/owned_process.py adapter (CORE)
purpose: Couple durable arm claims to one owned launch without duplicate execution.
signatures:
  - New `claim_arm(conn, arm_id, expected_revision) -> Attempt`, `run_attempt(attempt_id)`, `cancel_attempt(...)`, `reconcile_attempt(...)`.
  - Existing `OwnedProcess(directory, assignment)`, `spawn(argv, *, cwd, env, output, pass_fds=())`, `record()`, `cleanup()`.
  - Existing `execute(argv, *, cwd, env, output, directory, assignment, timeout)`.
inputs / outputs: Admitted frozen arm → owned process journal and settlement evidence.
state changes: Attempt `claimed → launch_intent → running → settling → terminal`; uncertainty becomes `settlement_unknown`. Arm remains noncomplete while any attempt is unsettled.
control flow:
  1. Allocate unique attempt directory and `OwnedProcess` token before launch; persist exact assignment, token, receipt/journal paths and supervisor identity.
  2. Acquire an exclusive per-attempt supervisor lock; then SQLite `BEGIN IMMEDIATE`.
  3. Require admitted experiment, uncancelled arm, current revision, remaining frozen retry budget and no unsettled predecessor attempt.
  4. Insert attempt and CAS arm’s active-attempt reference. Commit is claim linearization.
  5. Recheck frozen resources, containment and deadline. Build argv only through the selected closed runner adapter.
  6. Transactionally CAS `claimed → launch_intent`, recording exact launch binding. Commit before `spawn`.
  7. Release database transaction; retain supervisor ownership lock while launching/supervising. Never hold SQLite while waiting for processes.
  8. Invoke existing owned bootstrap: `owned_child.main()` writes token/assignment/process identity before `execvpe`.
  9. Persist observed launch identity and `running` state. A crash before this publication remains recoverable from the prebound receipt.
  10. Wait with frozen deadline and cancellation checks. On interruption/timeout, invoke identity-checked owned cleanup; publish its receipt before terminal state.
  11. Do not mark completed from exit code alone. Require process-group settlement and, for native runs, authoritative inner worker/native closure evidence.
data flow: SQL attempt, process journal, launch receipt and result all bind the same attempt/token/assignment.
failure / error behaviour:
  - The current `execute` journal starts with `ownership=None`; R7 needs a narrow adapter that persists the preallocated token/receipt before spawning.
  - Journal `PENDING`, malformed/missing receipts or failed cleanup retain `settlement_unknown`.
  - Do not reconstruct an `OwnedProcess` with `process=None` and call `cleanup()` as proof: its current implementation reports that case complete.
edge cases:
  - Claim without launch intent may be cancelled/released after exclusive ownership and state validation.
  - Launch intent without receipt is uncertain, not proof of no launch.
  - Start/cancel race: cancellation before launch-intent CAS prevents launch; afterward cancellation requests cleanup.
  - Parent exit does not prove descendant closure.
integration points: `al.verification_process` aliases the shared owned implementation. Preserve existing consumers and public isolated runner behavior.

## harness/evaluation/recovery.py, results.py (CORE)
purpose: Restart safely and publish immutable result plus arm terminal state atomically.
signatures: New `inspect_attempt(conn, attempt_id)`, `publish_result(conn, attempt_id, result_ref, expected_revision)`.
inputs / outputs: Durable journal, identity observations, closure receipts and evaluator evidence → retained outcome.
state changes: No replacement while unsettled; immutable result insert and arm transition share one transaction.
control flow:
  1. On restart, inspect recorded attempts. Owner heartbeat/PID absence is not settlement.
  2. Acquire the attempt lock and validate launch token, assignment, identity and receipt provenance.
  3. If trusted journal proves completed cleanup, recover settlement idempotently.
  4. If process-group identity remains verifiable, use a narrow identity-checked recovery adapter to request termination and observe closure. Never signal a reused PID/group.
  5. If identity/closure cannot be established, retain unknown state and expose the exact operational obligation.
  6. After participant settlement, snapshot allowed output through bounded ordinary-file descriptors; reject aliases, unstable bytes and foreign paths.
  7. Run frozen independent checks in a separate owned evaluator context. Do not expose hidden checks to a live participant.
  8. Validate complete collected criterion IDs, evaluator origins, output inventories and all immutable bindings.
  9. Publish immutable result artifact durably, then transaction:
       require exact active attempt and settled state;
       insert result uniquely by attempt;
       insert measurements;
       CAS arm to its terminal result.
  10. Identical publication replay returns existing result; conflicting bytes refuse.
data flow: Orphan artifacts after pretransaction crash may be reconciled by exact digest; they never imply a committed result.
failure / error behaviour: Evaluator crash has its own retained attempt/settlement state. Participant output cannot author score, provenance or eligibility.
edge cases:
  - Crash after artifact publication: verify and finish the same publication.
  - Crash after SQL commit: return committed result, never rerun.
  - Retry requires settled predecessor, frozen retry permission and a new attempt ID. Retain all prior failures; never select only the best attempt.
  - Restart-spanning duration without trustworthy elapsed-time evidence becomes unavailable.
integration points: Keep immutable runtime pins; no hand-edited bridge/authority recovery.

## harness/evaluation/adapters.py, metrics.py, comparison.py (CORE)
purpose: Produce qualified paired evidence without converting simulation or favorable partial data into efficacy.
signatures: New `measure(result, frozen_method) -> list[Measurement]`, `compare_experiment(conn, experiment_id) -> Comparison`.
inputs / outputs: Complete arm matrix and immutable results → comparison class plus separate eligibility predicate.
state changes: Append immutable comparison/review bindings; no activation.
control flow:
  1. Maintain four evidence lanes:
       protocol tests;
       implementation cohort;
       public-command simulation;
       qualified native engineering.
  2. Reuse `al.benchmark_verify.compare(...)` only for its controlled implementation-cohort semantics. Its identical fixture overlay and runtime-origin checks remain mandatory.
  3. Preserve `al_replay.run_case(package, case)` outputs as `simulation=True`; replay is not native engineering efficacy.
  4. Native adapters must bind exact selected bridge and evaluator source before dispatch and verify imported origins afterward, following `worker.verify_source` and `live.module_receipt`.
  5. Collect each metric with value/unit/numerator/denominator/status/scope/source refs/method version:
       criteria from frozen independent checks;
       completion from criteria plus required independent review;
       tests from actual setup/call/teardown collection, with skips/xfail/empty explicit;
       violations from demonstrated breaches, distinct from prevented attempts;
       interventions from actual operator actions;
       retries from retained attempts;
       tool calls from qualified boundaries;
       duration through settlement;
       recovery/escalation from typed triggers and declared populations.
  6. Scoped native usage remains unavailable where `usage.observe_usage(...)` refuses. Public telemetry is not automatically attributable child usage. No bytes-to-tokens conversion.
  7. Cost requires qualified quantities and frozen pricing basis; distinguish measured charge from derived estimate.
  8. Enumerate all required pairs before aggregation. Failed, missing, contaminated and unsupported arms remain in the declared population.
  9. Apply frozen missing/infra/stopping policy. Missing mandatory evidence cannot pass; optional unavailable metrics remain null.
  10. Evaluate safety/privacy/approval/isolation and material acceptance regressions before improvement thresholds.
  11. Produce `invalid`, `inconclusive`, `regression`, `no_improvement` or `improvement_supported`, with explicit reasons. Eligibility additionally requires complete mandatory evidence and independent review bound to exact comparison/result hashes.
data flow: Treatment-specific purpose determines metric benefit; a regression test can establish defect detection and repaired success without claiming speed improvement.
failure / error behaviour: Source/model/tool/policy drift invalidates controlled comparison. No favorable denominator filtering.
edge cases: An expected defective baseline may fail; candidate acceptance and frozen safety requirements still apply. Lower duration cannot override a material regression.
integration points: DEC-0052. R7 publishes evidence only; no promote, activate or rollback action.

## harness/evaluation/exposures.py, reproduction.py (CORE)
purpose: Make contamination and reproduction claims independently inspectable.
signatures: New `record_exposure(...)`, `qualify_holdout(...)`, `qualify_reproduction(...)`.
inputs / outputs: Immutable inventories, participant/candidate lineage, delivery and reconstruction evidence → qualification plus gaps.
state changes: Append-only exposure records keyed to case version, participant/run and purpose.
control flow:
  1. Record development use, author/reviewer access, participant delivery and evaluator-only disclosure separately.
  2. Candidate-design exposure disqualifies unseen-holdout claims for that lineage.
  3. Reserve exposure before disclosure; interruption cannot erase it.
  4. Recheck exposure eligibility before admission and comparison. Postfreeze contamination changes eligibility, not the frozen manifest.
  5. Faithful reproduction requires captured task/source/environment/tools/provider/policy/external-response inputs and actual reproduction of the defined symptom.
  6. Missing inputs yield simulation/not-reconstructable with exact gaps.
data flow: Evaluator-only inventories remain protected; public views expose references and qualification, not answers.
failure / error behaviour: Unverifiable exclusion is unknown/ineligible, never clean.
edge cases: Independent reviewer access is recorded and cannot silently become participant context.
integration points: R1 closed incident facts usually do not supply complete reproduction inputs.

## harness/dashboard evaluation actions/views/client state (CORE); fixtures/styles/docs (SUPPORT)
purpose: Expose real frozen state and deliberate operator controls.
signatures: New focused evaluation actions/query adapters; extend existing `HarnessHandler.do_POST`, `WRITES`, `_refusal`.
inputs / outputs: Bounded explicit prepare/start/cancel requests → persisted outcomes.
state changes: Operator actions only; reads are side-effect-free.
control flow:
  1. Preserve Host/Origin/JSON checks and impose body caps before `_body`.
  2. Bind request IDs/revisions; exact replay returns prior response, conflicting reuse refuses.
  3. Show controlled differences, every arm/attempt, settlement unknown, missing metrics, exposure and review eligibility.
  4. Fence stale client responses by experiment/task selection; cancellation acknowledgment is not settlement.
failure / error behaviour: No numeric “approved” badge or hidden launch on GET.
edge cases: No promotion endpoint.
integration points: Engineer owns correctness-sensitive client state; Builder follows frozen contracts for fixtures, styling, docs and browser verification.

TESTS_NEEDED
- `tests/test_evaluation_protocol.py`: real R6 consumer→freeze→paired owned arms→checks→comparison; R7-02/03/04/09/10.
- `tests/test_evaluation_recovery.py`: concurrent claims, every launch/publication crash window, cancel race, PID reuse, journal `PENDING`, no unknown replacement; R7-04/10.
- `tests/test_evaluation_controls.py`: treatment drift, immutable evaluator, hidden-test access attempts, context/exposure uncertainty, explicit unsupported accounting; R7-03/06/08.
- `tests/test_evaluation_metrics.py`: complete denominators, missing/null values, skips/xfail/empty collection, safety/regression precedence, frozen retries; R7-05/06/09.
- Concrete benchmark fixtures: asserted runnable coverage for all thirteen categories; R7-01/07.
- Existing cohort/replay/Knowledge tests: preserve lane labels, source-origin binding, read-only study and native observer refusals; R7-12.
- HTTP/browser tests: authorization boundaries, no activation routes, restart views, stale responses, cancellation and desktop/narrow alignment; R7-10/11.

EARLY_SUPPORT_SAFE
- none

DEFERRED_SUPPORT
- Benchmark fixtures: wait for frozen registry/check contracts and runner capabilities.
- Dashboard styles/docs/browser fixtures: wait for actual state/query/action contracts.
- Migration inventory: allocate after preceding increments.
- R6 adapter: revalidate actual consumer source, signature, authorization and immutable bytes once R6 lands.

OPEN_QUESTIONS
- []
