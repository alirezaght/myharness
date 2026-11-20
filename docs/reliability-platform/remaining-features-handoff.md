# Remaining Reliability Platform Implementation

Continue the approved mission to evolve AL Harness into a self-improving engineering platform. The user approved the entire mission, corrected the blanket Knowledge acceptance gate, requested an isolated worktree, and subsequently said **“push to main”** and **“give me a handoff about the remaining features.”** Git publication of the implemented checkpoint is now authorized. The full improvement loop is not implemented yet.

## Current checkpoint

The isolated worktree is `/Users/alirezaghiastabari/.codex/worktrees/reliability-platform-audit/myharness`, branch `codex/reliability-platform-audit`. Its original base is `2ad8bed9bdcaf82212f5f5ab3c8585b79943ca9d`. R1–R4 have implemented reliability observations, correlated timelines, policy enforcement and typed contracts, with retained tests and bounded independent diagnostic reviews. Formal approval, native qualification and historical recovery debt must not be inferred from those scoped results.

R5a implements six actual incident source families, an immutable catalog, bounded legacy projection, investigation revisions, related-symptom groups, operator corrections and published-output verdicts. The dashboard uses real persisted data. Facts, hypotheses, historical Knowledge membership and local operator attribution remain distinct from runtime approval or proven root cause.

Latest recorded R5a checks include new desktop/narrow Chrome cases PASS2, existing incident/timeline Chrome regressions PASS4, incident API PASS8, compatibility/migration PASS123, foundation PASS37, parity PASS1, JavaScript syntax PASS and lint PASS. E51's 69 lint findings in the new browser test were repaired; E52 reran the browser cases after those test-file changes and passed. Source-closure has now passed53 tests (E56), alongside foundation37 (E54) and parity1 (E55). The original R5a writer closed successfully after E57 format PASS687. Independent review of the integrated candidate and publication integration remain pending. Consult the actual ledger for currency; older PASS results can be stale after later source changes. There is no full-mission or production-readiness claim.

Read `original-mission.txt`, `implementation-authorization.json`, `approved-decisions.json`, `implementation-progress.md`, `verification.md`, `r5a-api-contract.md` and the evidence manifests in this directory. Preserve their failure history.

## Finish integration and publication first

Fetching `origin/main` returned `6b1da29`. Main is substantially ahead of the worktree base, including AL/runtime/storage, planning sessions, investigation workflows and Knowledge changes. Do not overwrite those changes or force-push.

The two streams use overlapping migration numbers: main adds `0019_investigation_sources.sql` and `0020_planning_sessions.sql`; the unpublished reliability checkpoint adds `0019_reliability_observations.sql` through `0023_incident_catalog.sql`. Actual Architect inspection confirms duplicate numeric prefixes are rejected and applied identity is keyed by numeric version. Preserve main0019/0020 unchanged; provisionally allocate reliability additions0021–0025. A source-backed Designer must specify a transactional, idempotent ledger bridge for recognized checkpoint-only histories and partial prefixes, validating filenames/schema signatures, retaining data and refusing unknown/mixed histories. Preserve main's pre-migration SQL function registration and planning-session transaction guards. Test upgrades/data preservation. Do not blindly create R5b migration0024 from an old plan.

Complete the current writer/checks, preserve its authored checkpoint/evidence, then integrate current main in isolation through AL. The actual Architect classified this COMPLEX with Designer required because migration-history reconciliation and process/publication liability contracts are coupled. No new product approval is needed. Independent source review must cover the final integrated candidate; earlier source results do not automatically qualify it. Resolve semantic conflicts in typed contracts, runtime import/resource qualification and storage as well as textual conflicts. Revalidate source inventories/parity and run relevant tests on the integrated candidate. Obtain independent review before an ordinary fast-forward push to main. Verify the resulting remote commit before reporting it pushed.

At this handoff's preparation, **nothing from this checkpoint has been committed or pushed**. Update this paragraph only from actual Git results. After publication, continue from a fresh isolated worktree based on verified remote main; the old worktree preserves an unintegrated checkpoint and is not the merged source baseline.

## Dependency order

Main integration and checkpoint verification → R5b source collection → R6 proposals → R7 evaluation → R8 controlled activation → R9 Knowledge integration → R10 recovery → R11 provider qualification and final verification.

Revalidate every plan against actual merged predecessors. Main's new investigation/planning/recovery modules may already supply useful foundations; reuse them rather than implementing a parallel system.

## R5b Standalone AL incident collection

Route: **COMPLEX**, with actual corrected Architect and Designer work already prepared. Read the corrected source addendum and pseudocode before the older R5b plans. The old assumption that Harness incident tables can simply accept null Harness IDs is obsolete.

- Add a standalone domain partition behind the existing catalog facade. Preserve existing Harness tables and constraints. Standalone source runs have real repository/platform/session/run/pin identity and no invented Harness task, execution, epoch or approval. Use domain-correct investigation/group persistence and explicit configured-repository mixed selection.
- Implement fixed protected `incident capabilities`, `incident export` and `incident page` methods for an existing selected run/pin. Include terminal runs through a genuinely observational bootstrap. Do not invoke ordinary mutating load/save, truncate/create the original lock, upgrade old pins, scrape state as a fallback or fabricate an Attempt.
- Export bounded immutable sanitized pages and one manifest from an authenticated snapshot. Current Harness-bound receipts skip standalone runs; distinguish terminal facts observed at export from retained accepted artifacts and present availability. Keep terminal PASS facts too, so changing an old FAIL to PASS cannot evade conflict detection.
- Add a real standalone typed ContractError receipt producer before error normalization, in separate protected diagnostic storage. Never save a failed command's dirty state or claim no earlier effects occurred.
- Journal request/launch ownership before invoking the exporter. Require actual qualified main/helper completion, retain the complete validated export before SQLite enrollment, and ingest source facts, incidents and receipts atomically. Exact request/export/source-key replay is idempotent; changed immutable facts conflict. Source disappearance after retention does not erase evidence. Unknown closure stays unknown and prevents unsafe relaunch.
- Add real source selection, preview, collection and provenance to the existing dashboard. GET/startup remain read-only and legacy projection remains bounded and late-row safe.

Acceptance: both AL distributions through the real protected path; domain/FK isolation; unsupported/foreign pin refusal; confined reads and symlink/hardlink/FIFO/size/secret-canary attacks; snapshot consistency; every retention/commit/response crash boundary; source removal; request conflicts; desktop/narrow functional and visual checks.

## R6 Automatic evidence to improvement proposals

Route: **STANDARD**. Consume actual canonical incidents/investigation revisions, including R5b domains.

Implement a durable bounded automatic drafting trigger outside tool-dispatch transactions. A button or unused helper alone is insufficient. New qualifying evidence should create/revise an inactive proposal automatically, with stable cursor/replay/dedup behavior.

Support all ten proposal types: instruction/guide, deterministic validator, regression test, permission constraint, handoff contract, context retrieval, planning checklist, orchestration/routing, observability sensor and escalation policy. Each version needs evidence, scope/owner, proposed change, measurable expected benefit, side effects, evaluation plan, rollback plan and approval impact. Support consolidation, supersession, withdrawal and retirement.

A known successful refusal should suggest preserving that control with a regression test; it must not suggest weakening permission. Unknown cause remains an investigation. Do not grow instruction files without bounds or silently activate proposals.

Provide immutable evaluation requests binding candidate, baseline, realization, environment/provider, metrics, thresholds, budgets and lane. The real consumer closes the R5a downstream-consumption obligation; R7 performs execution. Verify actual incident/revision → automatic draft → evaluation request → persisted UI readback, dedup/restart and authority boundaries.

## R7 Paired engineering evaluation

Route: **COMPLEX**. Revalidate retained Architect/Designer work against actual R6 contracts.

Implement thirteen runnable benchmark categories: feature development, bug fixing, refactoring, API change, database migration, multi-file work, architecture-sensitive work, ambiguity, tool failure, permission boundary, long-running work, multi-agent dependencies and historical failure.

Freeze task/source/environment/tools/provider/model/policy/evaluator, candidate realization, arm order/repetitions, metrics/thresholds and stopping rules before launch. Run both baseline/candidate arms through owned isolated execution with durable launch journals and no duplicate work while settlement is unknown. Protect evaluator-only tests/answers and record development/holdout exposures.

Keep four lanes distinct: protocol tests, deterministic implementation cohort, public-command simulation and qualified native engineering. Simulations or synthetic provider responses do not prove native engineering efficacy. Faithful replay requires the actual reconstructable inputs; otherwise label simulation or not reconstructable.

Measure real denominators and provenance. Missing required data cannot pass; unavailable usage/cost stays null. Safety, privacy, approval/isolation bypass and material acceptance regression override a favorable metric. Produce inspectable paired results and independent-review eligibility, not activation authority. Add the actual measured evaluation-result catalog adapter/schema here rather than a dummy R5a source.

## R8 Controlled activation canary and rollback

Route: **COMPLEX**; a source-backed Designer protocol is still required before implementation.

Bind the exact candidate/realization, evaluation, independent review, baseline, scope, canary bounds and rollback manifest. Use the existing sealed ApprovalGate/DecisionWorkflow for required human approval; an agent checkbox or local operator attribution cannot grant it.

Implement sticky version selection, selector CAS and execution/exposure reservation atomically. Preserve immutable applied receipts, old pins and in-flight execution behavior. Unknown exposure slots are not refunded speculatively. Monitor actual outcomes, stop/rollback future selections within the approved plan, and require the proper new approval for promotion.

The initial approved automatic realization is `retrieval-limits-v1` using existing QueryLimits. All ten proposal types can be authored/evaluated; application paths without a qualified applier must say unsupported and refuse rather than pretend to apply arbitrary source/instructions. Test approval/replay/drift/concurrency boundaries, monitoring, restart and real rollback.

## R9 Reliability Knowledge and graph integration

Route: **STANDARD**. Extend the existing KnowledgeStore/snapshots/assertions, provenance, citations, FTS, graph and Explorer. Do not create another memory platform or import a separate Knowledge chat.

Project incidents, interpretations, proposals, evaluations and activation/rollback facts with version, confidence, correction and supersession. Use bounded revision/CAS incremental processing with exact replay; timestamps alone must not lose late records. Preserve visibility, task/repository scope, review and purge behavior.

Implement the eight planned query recipes and crosslinks. Retain their bounded retrieval limits: eight anchors, forty assertions, two graph hops, two hundred edges and a32KiB result bound where specified by the accepted plan. Explain evidence and historical/current availability. Memory supplies evidence, never execution/approval authority or assumed causality. Verify all entities/query forms, provenance, purge, late arrivals and actual Explorer behavior.

## R10 Bounded recovery and escalation

Route: **COMPLEX**; actual current-source Architect/Designer work is required for the coupled recovery protocol.

Reuse existing AL retry/rework/reassessment and continuation authority. Add durable recovery intents/actions/receipts and real budgets, repeated-signature detection, comparable-cycle stagnation and oscillation detection. Preserve trigger/evidence/checkpoint identity across cancellation, restart, timeout and parent loss. Never launch duplicate unknown work, reset credit/history, infer settlement from PID absence or replay effects whose outcome is uncertain.

Use actual R3 admission claims/helper ownership and source recovery NoReplay rules. Stop or escalate when bounded recovery cannot proceed. The existing R4 RR1 history is important: two returns were used; the Architect recommended allowance but the runtime refused with PROOF_ADMISSION_PENDING; ordinary implementation continuity subsequently proceeded. RR1 was not accepted, reset or magically resolved. Preserve that history and repair future protocol behavior through supported interfaces, not old-pin/state edits.

Verify real crash/CLI/restart paths, unchanged caps, no unsafe replay/refunds, actual stagnation and typed escalation. Internal workflow trouble is operational repair, not a generic user approval gate.

## R11 Provider qualification and final integration

Route: **STANDARD** unless actual new provider protocol/isolation work justifies COMPLEX.

Extend the existing NativeRuntime facade; keep Claude primary and fixed AL role/model assignments. Expose explicit provider capabilities and requested versus observed identity. Enforce actual configured/qualified profile before dispatch. Do not add a second launcher or silently substitute models/providers.

Observed installed versions during preparation were Claude2.1.329 and Codex0.170.0; re-observe exact versions/binary hashes. The checkpoint's execution allowlists do not qualify those versions; Codex0.170 authoring support is separate. Use explicit test-only candidate admission preserving production refusal, actual required native CLI/profile tests and then production allowlisting after qualification. An allowlist-only patch, downgrade, synthetic protocol result or permanent skip is insufficient.

Use actual scoped token/usage records and a frozen official pricing basis for estimates. Keep unavailable values null and estimates distinct from billing. Provider comparisons use the frozen R7 treatment and disclose confounders. Preserve actual host/isolation limits; Darwin proof does not prove Linux or general process-tree containment.

Complete the remaining dashboard/history comparisons, migrations, compatibility/security/browser checks, documentation/diagrams and independent review. Label unavailable hosted CI, native efficacy or auth-dependent proof honestly. Stop affected work on real authentication/permission failure; do not change credentials, install globally or bypass guards.

## Execution instructions for the next agent

Implementation goes through `$al`. Use the actual named Architect/Designer/Engineer/Builder roles and source-backed plans, with independent fresh source review. Do not add approval rounds for bookkeeping. Only a real material ambiguity needing the user pauses affected implementation.

Before database work read `/Users/alirezaghiastabari/.config/ai-profile/DATABASE_CONNECTIONS.md`. Atlassian remains read-only unless the user explicitly requests a specific write. Check Chrome functionality and visual/UX together. Do not edit during active checks or run a second conflicting writer.

Use `/private/tmp/al-reliability-verified-venv/bin/python` for local checks; the project venv had SQLite below the required minimum. R5a run is `reliability-r5a-20261120`, ID `c0b8fa62`, current Builder writer `r5a-dashboard-support-003` while checkpoint closure is in progress. Inspect actual writer/check status before taking ownership. Managed checks need unique outputs and complete generated/read handoffs; never edit protected state/pins or fabricate evidence. Descendant-settlement uncertainty is not closure or retirement credit.

Local continuation details are in `/private/tmp/al-reliability-continuation-20261120.json`. Portable plans live in `planning/`; immutable future task packages and corrected R5b/R11 preparation should accompany this handoff. Revalidate source bindings after main integration. The next feature to implement is R5b, not another acceptance interview or a restart from R1.

## Planning files and portable inputs

Read the repository copies of these actual inputs, retaining their provenance:

- `planning/publication-integration-architect.md`: current source-backed merge/publication plan. Designer is still required.
- `planning/r5b-actual-source-architect-addendum.md` and `planning/r5b-actual-source-designer.md`: supersede nullable-Harness assumptions in older R5b material. Migration numbers in the old design are provisional after main integration.
- `planning/r5b-engineer-readiness.md`: concrete modules/test recipes prepared read-only; not implementation or qualification.
- `planning/r5-r6-architect.md`: proposal and actual automatic-drafting requirements.
- `planning/r7-architect.md` and `planning/r7-designer.md`: paired evaluation; source revalidation remains necessary.
- `planning/r8-architect.md` and `planning/r10-architect.md`: obtain actual current-source Designer protocols before these complex implementations.
- `planning/r9-architect.md`, `planning/r11-architect.md` and `planning/r11-current-provider-qualification-preparation.md`.
- `remaining-task-packages/r5b.json` and `r6.json` through `r11.json`: immutable prepared accepted packages, with their original mission and criteria. They do not supersede newer explicit user Git-publication authorization.

These repository copies accompany the archived handoff; while the current writer is finishing that archive, the exact local originals below remain available. Until archived, exact local originals are `/private/tmp/al-publication-integration-actual-architect-001.md`, `/private/tmp/al-r5b-actual-r5a-source-architect-addendum-001.md`, `/private/tmp/al-r5b-actual-r5a-source-designer-pseudocode-001.md`, `/private/tmp/al-r5b-actual-engineer-readiness-001.md`, `/private/tmp/al-r11-current-provider-qualification-preparation-001.md`, and `/private/tmp/al-<increment>-package.json`. Do not silently substitute an older report if a corrected copy is missing.
