# Incident investigation and evidence-driven improvements

Host capture of actual AL-Architect plan `/root/architecture_audit`. SPEC_READY, STANDARD, Engineer then Builder sequentially, no Designer. Existing transactions, immutable records, request IDs and optimistic revisions suffice. R5 and R6 are consecutive working increments, not a single disconnected schema delivery. This is future guidance; no tests or implementation claimed. Full original mission remains authorized, no global Knowledge gate.

R5 delivers observed failure -> incident investigation -> related suggestions -> human correction. R6 delivers incident evidence -> safe automatic draft or explicit authored proposal -> immutable candidate version -> persisted evaluation request consumed through a real validated interface. Both use existing dashboard. Neither changes instructions, policies, source, permissions or execution approval. Promotion/rollback is separate R8.

## Existing seams and authority

R1 store publishes closed current-epoch facts; query currently says root cause not_assessed. Migration0019 restricts source incidents to two categories/open; leave those immutable. Investigation state is separate. Existing dashboard server POST boundary checks Host, Origin, JSON/content-length/allowlist. workspace_proposals supplies request replay/conflict/current-base transaction pattern. DecisionWorkflow is actual developer approval and must never be called by machine drafting. execution_artifacts publishes authenticated immutable reports/exact bytes. Existing Knowledge contributions represent attributed material; do not create another memory graph.

Local operator attribution is local_dashboard_operator, not invented named authentication. Historical correction uses existing localhost operator boundary, not stale execution writer privileges. R2 trace ref optional only when real; execution ID does not imply reconstructed trace. A supported proposal type means author/revise/read/request-evaluation path, not executing its proposed change. R7 consumes R6 requests; publication alone is not evaluation.

## R5 investigation revisions

Add incident_investigation_heads(incident_id PK/FK,revision,revision_id), append-only incident_investigation_revisions(revision_id PK,incident_id,revision,parent_revision_id,schema_version,actor_kind,producer_ref,request_id,created_at,document), unique incident/revision and incident/request. Index time/revision.

Closed document contains incident ID, immutable observation ref/optional actual trace, category/classifier ID+version+provenance, facts as observed refs without raw diagnostics, hypotheses with bounded explanation/supporting+contradicting evidence and ordinal unassessed|low|medium|high confidence plus provenance, verified repository-relative affected components or explicit unverified claims, controls with held|failed|unknown, corrective suggestions, investigation status open|investigating|resolved|dismissed, rationale/evidence for resolution/dismissal, and separate disputed-field corrections/reasons. Resolved means the recorded actor resolved the investigation; never deployment or verified improvement.

Classify only supported evidence: AUTHORITY_REJECTED proves unauthorized attempt/control held; DISPATCH_REJECTED is rejected dispatch with unknown cause unless typed validator supplied a safe code; OS/process/internal dispatch errors are operation failures, not failed tests/hallucinated APIs. Additional category adapters bind authoritative producer: verification/evaluation -> test/CI/staging/implementation failure; review finding against criterion -> architecture violation/spec drift/incorrect deliverable; R4 rejection -> invalid handoff; R3 accounting -> budget exhaustion; actual bounded retry/edit facts -> excessive retries/unsuccessful edits; explicit human evidence-backed verdict -> rejected delivery; authored evidence-backed investigation -> missing context/wrong tool/hallucinated API/dependency. Wire adapters when authoritative producers exist. Unavailable coverage explicit; no raw-log import endpoint.

Model-authored analysis uses authenticated output provenance/R4 contracts and remains hypothesis. Any model analysis is an explicitly dispatched bounded existing-provider task, never hidden calls or wider observers.

## R5 conservative grouping and operator correction

Add incident_group_suggestions(group_id,algorithm_version,safe_key,created_at), incident_group_members(group_id,incident_id,provenance,decision_revision), append-only accept/reject/split/supersede decisions. Explicit safe-key fields: repository scope, normalized category/code, tool/role, known policy/spec version. Cross-version similarities remain suggestions with differences visible. Unique algorithm/input/incident deduplicates. Never merge/delete source incidents. Rejected suggestion cannot silently regenerate for same algorithm/input version. Grouping suggests shared symptoms, never shared cause.

Allowlisted `/api/reliability/` operator actions impose small body cap before reading. Correction contract: schema_version,task,incident_id,expected_revision,request_id,changes. Server derives repository/task ownership from configured database and incident. Historical epoch is evidence context only. Transaction: resolve scope; check request replay before revision; identical sanitized request returns prior result; conflicting reuse rejects; compare revision; validate allowed fields/evidence scope; append revision+update head atomically. No native correction operation, no _publication use or require_active_epoch bypass. Source observations/epochs/approvals/terminal execution unchanged.

## R6 ten real candidate types

Instruction/guide, deterministic validator, regression test, permission constraint, handoff contract, retrieval, planning checklist, orchestration/routing, observability sensor, escalation policy. Each has actual type-specific requirements (predicate/rejection behavior; trigger/assertion; permission delta; compatibility; retrieval scope; routing conditions; capture boundary; escalation condition), beyond a type enum.

Immutable candidate version contains ID/version/parent/schema, type/owner/bounded scope, exact incident/group/investigation revision refs, observed facts/hypothesis refs/deterministic rationale, proposed change/target refs/baseline hashes when available, measurable purpose/benefit, affected workflows/side effects, baseline-candidate evaluation plan+safety checks, rollback strategy, approval requirements, related/superseded/duplicate refs and completeness limits. Permission/approval/routing/sensor proposals explicitly mark security impact. All inactive, human activation remains required.

Bounded deterministic drafting pass consumes new accepted incident/investigation revisions outside R1 dispatch transaction. Persist work identity/result atomically with candidate publication: precommit crash retries safely, committed result reused. Initial complete example: verified authority rejection + known control -> regression-test draft preserving that refusal, exact evidence/expected denial. Never propose allowing an operation just because it was denied. Templates require measurable target/valid evaluation strategy. Unknown generic OS/internal cause yields needs_investigation rather than invented rule. Unique template ID/version/input revision set/type/scope deduplicates safe IDs/metadata. Link equivalent existing candidates instead of regenerating.

Tables: improvement_candidates(candidate_id,owner,scope,current_version,lifecycle_state), improvement_candidate_versions(candidate_id,version,parent_version,document,created_at,producer_ref), improvement_relations(source_version,target_version,kind), improvement_requests for operator idempotency. Lifecycle draft -> evaluation_requested with superseded|withdrawn|retired metadata states. Edit creates immutable new version; consolidation references every absorbed candidate. Retirement preserves evidence and does not disable deployed rules (R8).

## R6 evaluation request producer and consumer

evaluation_requests(request_id,candidate_id,candidate_version,candidate_digest,status,created_at,document). Bind exact candidate, baseline/target refs, benchmark selectors and missing requirements, faithful_requested|simulation|new_regression mode, required environment/provider capabilities, metrics/safety/budgets/comparison criteria, and later execution approval prerequisites. Publish pending, launch nothing. Nonexecutable candidates stay draft with structured readiness errors.

Real load_evaluation_request resolves exact persisted candidate bytes/refs, validates binding and returns typed immutable request or actionable refusal. R7 consumes it, never UI prose reconstruction. R6 tests real publication/consumer; completed evaluation remains R7.

## Existing dashboard and ownership

Facts/hypotheses/human corrections separate; related suggestions with accept/reject/split; revision history and conflict reload; candidate list/detail/versions; author/edit all ten types; readiness and Request evaluation; explicit proposed/inactive and evaluation pending/unavailable. Scoped side-effect-free reads, keyset pagination and sanitized search; category/group/status/type filters. Preserve task/epoch switch fencing, focus/drafts/disclosures.

Engineer: reliability investigations/grouping/failure_adapters; focused improvements contracts/store/drafting/evaluation-request package; additive next migrations; minimal reliability query links; actual public action/view validation needed for complete backend journey. Builder follows stable interfaces with existing server/frontend/static styles, migration inventory and public HTTP/browser fixtures/docs. No parallel writers while contracts change. R1 runtime writer should not need investigation/correction changes. Trusted snapshot closure includes any required new modules explicitly.

Migrations additive local SQLite after R2–R4, preserve existing R1 rows/repeated startup; no table rebuild/historical backfill/subscription/provider deploy/staging activation. Migrate only authorized writable startup, never GET. Existing WAL/Python/SQLite prerequisite remains. CI locked sync, Ruff lint/format, Linux/macOS nonbrowser/build, browser workflow sandboxed Chrome; no declared job timeout overrides. No external DB/provider authentication/private transcript/persistent service prerequisite for these fixtures.

## Acceptance and real tests

- R56-01 source failures acquire investigations without source-fact mutation.
- R56-02 facts, hypotheses/confidence, human corrections distinct structurally and visually.
- R56-03 grouping reproducible/bounded/correctable, never shared-cause assertion.
- R56-04 trusted historical operator correction without weakening runtime fencing.
- R56-05 actual failure-to-draft automatic/idempotent path.
- R56-06 all ten versioned types include evidence/scope/purpose/side effects/evaluation/rollback/approval.
- R56-07 usable ownership/dedup/consolidation/supersession/retirement.
- R56-08 immutable request binding plus actual validated consumer.
- R56-09 unsupported category/evidence/replay explicit; no invented diagnosis/evaluation.
- R56-10 no activation/authority-changing operation.
- R56-11 real dashboard investigation/correction/history/readiness with scope/security/Chrome visuals.
- R56-12 existing Knowledge refs/contributions later, no second graph or global gate.

Drive real denial -> R1 incident -> deterministic investigation/group -> inactive regression draft -> candidate -> request -> load. Correct retired incident and compare original observations/epochs/approvals unchanged. Test exact/conflicting replay, concurrent stale revisions, rejected group rerun, all ten type-specific author/revise/read/request checks, unknown cause/missing evaluation readiness, Host/Origin/body cap/foreign scope/native correction refusal, secret canaries in keys/drafts/errors, no raw log import, upgrade/restart dedup, task-switch delayed responses/edit conflicts/paging/revision views and desktop/narrow alignment/clipping/authority labels. Scope exact sources/fixtures/manifests/CI before checks. No tests executed by this plan.
