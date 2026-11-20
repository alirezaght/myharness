SPEC_STATUS: SPEC_READY

TASK_CLASSIFICATION: STANDARD
CLASSIFICATION_REASON: R5/R6 use existing SQLite transactions, immutable records, explicit request IDs and dashboard operator actions. They introduce no execution authority or autonomous activation protocol.
IMPLEMENTER: both
IMPLEMENTER_REASON: Engineer owns persistence, classification, correction and candidate/request validation; Builder completes dashboard integration, migration compatibility and public-boundary tests.
DESIGNER_REQUIRED: NO
DESIGNER_REASON: The bounded flow below follows inspected transaction and optimistic revision patterns. Promotion, rollback and autonomous execution remain separate R8 work.

SPEC_SUMMARY

Deliver two consecutive working increments:

1. **R5:** observed failure → incident investigation → related-incident grouping → human correction.
2. **R6:** incident evidence → safe automatic draft or explicit authored proposal → immutable candidate version → persisted evaluation request.

Both appear in the existing dashboard. Neither modifies instructions, policy, source code, permissions or execution approval.

ASSUMPTIONS

- A1: Existing R1 facts remain immutable. Investigation and correction records are separate interpretations.
- A2: R2 trace references are optional links until an actual trace exists. Historical execution IDs do not imply reconstructed traces.
- A3: Existing localhost dashboard operator authority is sufficient for investigation edits, following its established write boundary. Attribution is `local_dashboard_operator`, not a fabricated authenticated human identity.
- A4: Supported proposal type means an end-to-end author/revise/read/request-evaluation path, not an executable implementation of every proposed change.
- A5: R7 must consume these evaluation requests. R6 completion proves durable request publication and consumer validation, not completed evaluation.

INVARIANTS

- I1: Runtime facts, machine hypotheses and human corrections remain separately attributable.
- I2: Similar symptoms establish a grouping suggestion, not common root cause.
- I3: Historical correction cannot mutate runtime observations, execution epochs, approval records or terminal execution state.
- I4: Runtime producers retain R1’s current-epoch restrictions. No stale-writer exception is added.
- I5: Candidate versions and evaluation requests bind exact sanitized inputs; changed evidence creates a new version or explicit supersession.
- I6: No candidate, request or investigation verdict activates a change.
- I7: Grouping and deduplication use closed safe fields and stable IDs—not raw diagnostics, argument values or hashes of secrets.
- I8: Failure classification must identify the supporting producer and coverage limitations. Absence of evidence is not success.

IMPLEMENTATION_PLAN

### Existing seams and constraints

- `harness/reliability/store.py:57,115` publishes closed observations and one incident per denied/failed observation.
- `harness/reliability/query.py:18` currently projects every root cause as `not_assessed`.
- `harness/migrations/0019_reliability_observations.sql` restricts incidents to two categories and `status='open'`. Do not overwrite these source facts to implement investigation state.
- `harness/dashboard/reliability_view.py` supplies scoped read-only queries.
- `harness/dashboard/server.py:401,490` restricts POST routes, Host, Origin, JSON content type and Content-Length before dispatch.
- `harness/workspace_proposals.py:139` provides the existing request-ID/replay/conflict and current-base transaction pattern.
- `harness/decision_workflow.py:46,149` records real developer verdicts. A machine draft must not call these approval paths.
- `harness/execution_artifacts.py:133,196` provides immutable authenticated report publication and exact-byte resolution.
- `harness/knowledge/contributions.py:71` already supports attributed contributions. R5/R6 must not build a second graph or lesson memory.

### R5.1 — immutable investigation revisions

Add separate investigation storage, leaving R1 tables intact:

- `incident_investigation_heads(incident_id PK/FK, revision, revision_id)`
- `incident_investigation_revisions(revision_id PK, incident_id, revision, parent_revision_id, schema_version, actor_kind, producer_ref, request_id, created_at, document)`
- Unique `(incident_id,revision)` and `(incident_id,request_id)`.
- Index incident/revision and creation time.

Closed document:

- `incident_id`, immutable observation reference and optional actual trace reference.
- `classification`: category, classifier ID/version and provenance.
- `facts`: references to observed records; no copied raw diagnostic bodies.
- `hypotheses`: bounded explanation, supporting/contradicting evidence references, `confidence` of `unassessed|low|medium|high`, and provenance. Confidence is ordinal judgment, not calibrated probability.
- `affected_components`: verified repository-relative references or explicitly `unverified` claims.
- `controls`: control reference and `held|failed|unknown`; an authority rejection ordinarily proves a control held.
- `corrective_actions`: suggestions, not commands.
- `status`: `open|investigating|resolved|dismissed`, with rationale and evidence references for resolution/dismissal.
- `corrections`: fields disputed, corrected interpretation and reason; original facts remain visible.

`resolved` means the investigation is resolved by its recorded actor. It must not mean an improvement was deployed or verified.

### R5.2 — trustworthy producers and category coverage

Initially classify existing R1 observations directly:

- `AUTHORITY_REJECTED` → unauthorized-operation attempt / control held.
- `DISPATCH_REJECTED` → rejected dispatch; cause remains unknown unless an existing typed validator provides a specific safe reason code.
- OS/process/internal dispatch errors → operation failure; do not infer failed tests, dependency hallucination or incorrect implementation.

Add adapter contracts for the mission’s remaining categories:

| Evidence producer | Permitted classification |
|---|---|
| Bound verification/evaluation result | Failed test, CI/staging failure, incorrect implementation |
| Bound review finding against a specific criterion | Architecture violation, specification drift, incorrect deliverable |
| R4 contract rejection | Invalid handoff |
| R3 accounting/limit decision | Budget exhaustion |
| Actual bounded retry/edit records | Excessive retries or repeated unsuccessful edits |
| Explicit human verdict with evidence | Human-rejected deliverable |
| Authored investigation supported by referenced evidence | Missing context, wrong tool, hallucinated API/dependency |

Do not manufacture these from generic process errors. Unavailable producers appear in coverage metadata as unavailable. Wire each adapter when its authoritative producer exists; no arbitrary “import raw log” endpoint.

Machine-authored analyses must arrive through existing authenticated output provenance and R4 contracts. They remain hypotheses. Model analysis requires an explicitly dispatched bounded existing-provider task; no hidden provider call or observer expansion.

### R5.3 — conservative grouping

Add:

- `incident_group_suggestions(group_id, algorithm_version, safe_key, created_at)`
- `incident_group_members(group_id, incident_id, provenance, decision_revision)`
- Append-only operator decisions accepting, rejecting, splitting or superseding suggestions.

Safe key consists of repository scope, normalized category/code, tool, role and relevant known policy/specification version. Store these fields explicitly. Similarity across different policy/spec versions is a suggestion with that difference shown.

Automatic grouping is idempotent by `(algorithm_version,safe_key,incident_id)`. It never merges or deletes source incidents.

Use group membership plus correction revisions as candidate inputs. Rejecting a suggestion prevents its silent regeneration under the same algorithm/input version.

### R5.4 — actual operator correction boundary

Add explicit allowlisted dashboard actions under `/api/reliability/`, with a small request-size cap before reading the body.

Correction request:

`schema_version, task, incident_id, expected_revision, request_id, changes`

Server derives repository/task ownership from its configured database and the incident’s execution. Historical epoch is evidence context, not current writer authority.

Inside one transaction:

1. Resolve incident/task scope.
2. Check request-ID replay before revision comparison.
3. Return the existing result for identical sanitized request content.
4. Reject conflicting request-ID reuse.
5. Compare `expected_revision`.
6. Validate allowed correction fields and evidence scope.
7. Append revision and update head atomically.

No native operation exposes this operator action. Do not call `_publication()` or bypass `require_active_epoch()`.

### R6.1 — ten usable proposal types

Support these exact types through one typed versioned candidate service:

1. Instruction/guide update.
2. Deterministic validation rule.
3. Regression test.
4. Permission constraint.
5. Handoff contract improvement.
6. Context retrieval improvement.
7. Planning checklist update.
8. Orchestration/routing policy change.
9. Observability sensor.
10. Escalation policy adjustment.

Each has actual type-specific required fields—for example validator predicate/rejection behavior, test trigger/assertion, permission delta, contract compatibility, retrieval scope, routing conditions, sensor capture boundary or escalation condition.

Common immutable candidate document:

- Candidate ID/version, parent version, schema version.
- Type, owner and bounded scope.
- Exact incident/group/investigation revision references.
- Observed facts, hypothesis references and deterministic rationale.
- Proposed change, target references and target baseline hashes where available.
- Measurable purpose and expected benefit.
- Affected workflows and potential side effects.
- Evaluation plan with baseline/candidate comparison and required safety checks.
- Rollback strategy.
- Approval requirements.
- Related/superseded/duplicate candidate references.
- Completeness limitations.

Permission, approval, routing and sensor proposals explicitly identify security impact. All candidates remain inactive; human activation is still required.

### R6.2 — automatic safe drafting

Run a bounded deterministic drafting pass over newly accepted incident/investigation revisions. It does not run inside R1’s tool-dispatch transaction.

Persist work identity and result in the same transaction as candidate publication. A crash before commit retries safely; a committed result is reused.

Initial complete automatic example:

- Verified authority rejection + known control identifier → regression-test draft whose purpose is to preserve the observed refusal.
- Include exact evidence and expected denial. Never propose allowing the rejected operation merely because it failed.

A template must name a measurable target and valid evaluation strategy. Generic internal/OS failures without sufficient context produce `needs_investigation`, not a speculative rule.

Use unique `(template_id,template_version,input_revision_set,type,scope)` for deduplication. This key references safe IDs and closed metadata. Equivalent existing candidates are linked, not repeatedly generated.

### R6.3 — versioning, consolidation and retirement

Add:

- `improvement_candidates(candidate_id, owner, scope, current_version, lifecycle_state)`
- `improvement_candidate_versions(candidate_id,version,parent_version,document,created_at,producer_ref)`
- `improvement_relations(source_version,target_version,kind)`
- `improvement_requests` for idempotent operator actions.

Candidate lifecycle: `draft → evaluation_requested`, with `superseded|withdrawn|retired` metadata states. These states describe proposal lifecycle only.

Editing creates a new immutable version. Consolidation creates a new version referencing every absorbed candidate. Retirement never removes source evidence or disables a deployed rule; deployed-rule retirement belongs to R8.

### R6.4 — evaluation request producer and consumer contract

Persist:

`evaluation_requests(request_id,candidate_id,candidate_version,candidate_digest,status,created_at,document)`

Request binds:

- Exact candidate version.
- Required baseline/target references.
- Benchmark selectors and explicit missing benchmark requirements.
- Reproduction mode: `faithful_requested|simulation|new_regression`.
- Required environment/provider capability constraints.
- Metrics, safety checks, budget limits and comparison criteria.
- Approval prerequisites for any later execution.

R6 publishes `pending`; insufficiently executable candidates remain drafts with structured readiness failures. Request publication launches nothing.

Provide a real `load_evaluation_request(request_id)` consumer that resolves candidate bytes, verifies references and returns a typed immutable request or actionable refusal. R7’s lab must consume this service, not reconstruct requests from UI text. R6 tests this consumer against real persisted requests; completed evaluation remains R7’s responsibility.

### R5/R6 dashboard

Extend the existing reliability view with:

- Facts, hypotheses and human corrections visually separated.
- Related-incident suggestions with accept/reject/split controls.
- Revision history and conflict/reload handling.
- Candidate list/detail/version history.
- Draft creation/editing for all ten types.
- Evaluation readiness and explicit “Request evaluation”.
- Persistent labels: proposed/inactive; evaluation pending/unavailable.

Reads remain scoped and side-effect free. Use stable keyset pagination; filters include category, group, status and proposal type. Search only sanitized fields. Preserve R1’s task-switch and stale-response safeguards.

INFRASTRUCTURE_CONSTRAINTS

- Additive local SQLite migrations are required; allocate numbers after stabilized R2–R4.
- No historical backfill, table rebuild, subscription or provider deployment is necessary.
- Use existing `harness/db.py` migration/transaction paths, WAL and supported SQLite prerequisite.
- Inspected CI uses locked `uv sync`, Linux/macOS pytest, Ruff and build. Browser workflow provisions sandboxed Chrome and runs `uv run pytest -m browser`.
- These workflows have no declared job timeout override; runner/project limits were not inspected and must not be invented.
- No staging/pilot activation flag is introduced. All proposal activation remains absent.
- Migration tests must cover existing R1 data and repeated startup. Run migrations only through an authorized writable startup path, never GET.

CORE_SCOPE: Investigation/correction persistence, safe grouping, producer adapters, deterministic drafts, immutable candidate versions and evaluation-request publication/consumption.

SUPPORT_SCOPE: Dashboard, migrations/upgrade fixtures, public HTTP tests, browser workflow and documentation.

INTEGRATION_POINTS: R1 observation references; R2 trace links; R3 budget facts; R4 typed reports; existing dashboard POST boundary; later R7 consumer; later Knowledge contribution adapter.

EDGE_CASES: Historical/terminal execution correction, forged task scope, stale revision, duplicate request, request conflict, simultaneous edits, unknown trace, rejected group regeneration, missing target baseline, unsupported category evidence, stale candidate input, retired candidate, raw-secret sentinel, withdrawn evaluation request.

RISKS: Treating blocked operations as broken controls; laundering model hypotheses into facts; treating local operator attribution as named authentication; automatic duplicate rule growth; implying evaluation or activation from request creation.

WRITE_OWNERSHIP

CORE_FILES:
- New `harness/reliability/investigations.py`, `grouping.py`, `failure_adapters.py`.
- New `harness/improvements/` contract/store/drafting/evaluation-request modules.
- Additive migrations.
- Minimal `harness/reliability/query.py` integration.

SUPPORT_FILES:
- New dashboard action/view modules.
- `harness/dashboard/server.py`, reliability/improvement static modules and styles.
- Focused persistence/action/browser tests.

SHARED_FILES: Migration contracts, query response contracts and focused tests; Engineer first, Builder afterward. R1 runtime observation writer should require no change for investigation/correction.

PARALLEL_ASSESSMENT

BUILDER_EARLY_WORK: none.
BUILDER_DEFERRED_WORK: UI and integration fixtures after persistence/API stabilization.
INTERFACE_STABILITY: Investigation revisions, candidate schemas and request interfaces are new.
SHARED_STATE_COMMANDS: Migration and broad browser verification sequenced.
PARALLEL_SAFE: NO.
REASON: UI and verification consume the new core contracts.

TEST_PLAN

- T1: Actual denied operation → R1 incident → deterministic investigation/group → inactive regression draft → immutable candidate → persisted evaluation request → consumer load.
- T2: Operator corrects historical incident after execution retirement; original observations/epochs/approvals remain byte-for-byte unchanged.
- T3: Same request replay succeeds once; conflicting replay and concurrent stale edits return conflict without partial writes.
- T4: Similar symptoms group as suggestions without root-cause assertions; rejection survives rerun.
- T5: Every improvement type completes author/revise/read/request-validation flow with meaningful type-specific checks.
- T6: Unknown causes and missing executable evaluation inputs yield explicit investigation/readiness gaps.
- T7: Cross-origin, wrong Host, oversized, wrong-scope and native-writer correction attempts fail.
- T8: Secret sentinels never enter grouping keys, generated drafts or error responses; unrestricted logs cannot be imported.
- T9: Migration preserves R1 records; restart/replay does not duplicate drafts or requests.
- T10: Chrome checks task switching, stale responses, edit conflicts, paging, revision comparison and pending evaluation; inspect desktop/narrow layouts for clipping, alignment and confusing authority labels.

VERIFICATION_COMMANDS

Register focused tests for investigations, improvements, evaluation requests and dashboard actions after files exist. Preserve existing CI commands:

- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run pytest -m "not browser"`
- `uv run pytest -m browser`
- `uv build`

No tests were executed for this plan.

EVIDENCE_PLAN

Focused evidence must include actual producer→persistence→API journeys, not schema-only fixtures. Scope includes migrations, runtime fact contracts, revision/candidate services, dashboard body/origin handling, static consumers, test fixtures, dependency manifests and CI recipes. Broad browser evidence requires screenshots plus functional and visual findings.

RUNTIME_PREREQUISITES

Supported Python/SQLite, locked environment, disposable local databases, loopback access and sandboxed Chrome. No external database, provider authentication, private transcript or persistent service prerequisite.

ACCEPTANCE_CRITERIA

- AC1: Existing observed failures acquire inspectable investigations without altering their source facts.
- AC2: Facts, inferred causes, confidence and human corrections are visibly and structurally distinct.
- AC3: Grouping is reproducible, bounded and correctable; grouping never asserts shared cause.
- AC4: Historical correction uses the trusted operator route without relaxing execution fencing.
- AC5: At least one real deterministic failure-to-draft path works automatically and idempotently.
- AC6: All ten improvement types support versioned proposals with evidence, scope, purpose, side effects, evaluation, rollback and approval requirements.
- AC7: Ownership, deduplication, consolidation, supersession and retirement are usable persisted operations.
- AC8: Evaluation requests bind immutable candidate versions and are consumed through a validated interface.
- AC9: Unsupported evidence/category/replay capabilities are explicit; no fabricated diagnosis or evaluation.
- AC10: No operation activates an improvement or changes runtime authority.
- AC11: Existing dashboard supports investigation, correction, candidate history and evaluation readiness with scoped security and verified visual behavior.
- AC12: Existing Knowledge integration is reused later through references/contributions; no second knowledge store or global gate is introduced.

Inspected source hashes for later routing refresh:

- `harness/reliability/store.py`: `b592c78777914145570680e015a329d7df6d3063e69c7fee8fccc9c95aa1c3a7`
- `harness/reliability/query.py`: `1a078ea2198c43ed0e8ff85074bb1badee885b02d57a78915de99e102c3a0f3e`
- `harness/dashboard/server.py`: `b7147e38d4743b4ed7ff8bedfe033e0ccd7027bf5a260544f4a6039bffa4c2fa`
- `harness/workspace_proposals.py`: `3cdb3b96dadb177a91b4fe6bd407e182211158b06f26c504d6d7e60b1d47639a`
- `harness/decision_workflow.py`: `4e8ebf35a15325a88564cb338d91d29275976afbc8d1ac8edd2b08e1212fafbc`

Refresh bindings before each increment’s protected admission. This is planning evidence, not implementation verification or approval.
