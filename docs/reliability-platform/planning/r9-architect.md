SPEC_STATUS: SPEC_READY

TASK_CLASSIFICATION: STANDARD
CLASSIFICATION_REASON: R9 extends the existing source adapters, assertion graph, snapshot publication and bounded retrieval. It adds no independent memory store or cross-store execution protocol.
IMPLEMENTER: both
IMPLEMENTER_REASON: Engineer implements source adapters, incremental publication and query contracts; Builder integrates Knowledge Explorer and completes compatibility/security/browser coverage.
DESIGNER_REQUIRED: NO
DESIGNER_REASON: Existing source-version identities, snapshot compare-and-swap, visibility filtering and citation revalidation provide the necessary mechanisms. Preserve them rather than introducing a new indexing protocol.

SPEC_SUMMARY

Integrate reliability incidents, execution evidence, policies, proposals and evaluation results into the existing Knowledge graph. Support all eight requested reliability questions with cited, scope-filtered results and explicit uncertainty. Retrieved knowledge remains context, never execution authority.

DEC-0048 v2 explicitly removes any blanket Knowledge acceptance gate. Validate the specific interfaces consumed here. Do not import or resume the separate Knowledge chat’s unfinished work.

ASSUMPTIONS

- A1: Existing bounded keyword/entity/graph retrieval is the appropriate hybrid for this slice. No embedding service is necessary.
- A2: “Other agents” includes agents visible within the caller’s existing task/project permissions. Cross-task knowledge does not silently become project-visible.
- A3: Future R3/R5/R6/R7 adapters bind their actual stabilized public records. Their planned table names are not current APIs.
- A4: Reliability indexing is an operator/investigation projection of persisted records, not a historical execution writer.

INVARIANTS

- I1: One Knowledge store, one provenance system and the existing query authorization partition.
- I2: Index only allowlisted sanitized records, never operation arguments, transcripts, raw diagnostics or arbitrary artifact bodies.
- I3: A source-backed hypothesis proves that the hypothesis was recorded—not that its proposed cause is true.
- I4: Superseded, corrected, withdrawn and incomplete evidence remains explicitly distinguishable.
- I5: Publication cannot weaken source visibility, policy generation, retention/purge or review restrictions.
- I6: Retrieval, indexing and graph links cannot create approvals, capabilities, writer admission or deployment authority.
- I7: Missing source adapters and incomplete batches are coverage gaps, not empty-success results.

IMPLEMENTATION_PLAN

### Current implementation and source limits

Inspected in this isolated worktree:

- `knowledge/models.py`: `SourceVersion`, `Assertion`, `PreparedSnapshot`, `KnowledgeScope`, bounded `ContextRequest`/`QueryLimits`.
- `knowledge/adapters.py:21,154,212`: allowlisted structured sources, task-scoped enumeration and extraction.
- `knowledge/integration.py:24`: `refresh_with_records()` publishes structured additions with `carry_forward=True`.
- `knowledge/store.py:269,346`: `publish_snapshot()` checks expected active snapshot, policy generation and purges before atomic publication.
- `knowledge/query.py:95`: visibility/permitted-source partition precedes retrieval.
- `knowledge/query.py:205`: citation freshness checks reread source bytes.
- `knowledge/query.py:307`: existing keyword/entity seeds and bounded graph expansion.
- `knowledge/policy.py:39,47`: server-issued operator scopes and scope validation.
- `knowledge/integration.py:135`: assignment context uses existing authority-derived scope.
- `dashboard/static/knowledge.js`: existing refresh, query, status and evidence UI.
- R1 observations/incidents and R2 traces/process spans/timeline links exist in current source.

`HEAD` and locally known `main` both resolve to `2ad8bed9bdcaf82212f5f5ab3c8585b79943ca9d`. The inspected working-tree Knowledge difference is R2’s assignment timeline linkage in `integration.py`. This is local divergence information, not evidence about remote branches or the separate chat.

R3 is actively changing. R5/R6 and R7 are plans in `docs/reliability-platform/r5-r6-implementation-spec.md` and `r7-implementation-spec.md`, supplemented by the accepted canonical-source/R5b corrections. Their candidate/evaluation/catalog APIs must not be represented as already implemented.

### 1. Add reliability adapters to the existing pipeline

Create `harness/knowledge/reliability_sources.py` with narrowly typed source selection, canonical serialization, extraction and `current_bytes()` functions.

Reuse `SourceVersion` and existing evidence storage. Stable source identity:

`identity(project_id, source-domain, source-kind, stable-record-id)`

Version identity includes canonical sanitized bytes, adapter version and visibility. Actual source revision, timestamps, actor/provenance and authoritative identifiers remain in the source observation.

Register source kinds in:

- `extract.extract_source()`
- structured citation reread dispatch in `query._citation_current()`
- structured refresh orchestration.

Do not route these records through generic text extraction.

### 2. Entity and relation coverage

| Entity | Authoritative source and principal relations |
|---|---|
| Agents | Existing validated agent events plus R1 principal/role facts; `performed`, `assigned-to`. |
| Tasks | Existing task/requirement adapter; `governed-by`, `produced`. |
| Specifications | Actual approval/package version bindings; `governs`, `supersedes`. |
| Decisions | Existing decision extraction/registry references; `references`, `constrains`, version history. |
| Code components | Existing repository entities; verified component references from findings/candidates. |
| Dependencies | Existing static dependency extraction; preserve unresolved dynamic-dependency coverage. |
| Tool executions | R1 observation and R2 process identities; `performed-by`, `observed-in`, `produced-outcome`. |
| Incidents | R1 now; canonical R5 catalog when available; `evidenced-by`, `affects`, `related-suggestion`. |
| Root causes | R5 investigation hypothesis entities; `hypothesizes-cause`, supporting/contradicting evidence. |
| Tests | Validated evidence/check records and later benchmark cases; `checks`, `detected`, `regressed`. |
| Sensors | Actual classifier/capture method identity/version; `observed-by`, coverage limitations. |
| Policies | Actual policy digests and R3 decision records; `evaluated-under`, `denied-by`, `held`. |
| Improvement proposals | R6 immutable candidate versions; `addresses`, `targets`, `supersedes`, `consolidates`. |
| Evaluation results | R7 measured comparisons; `evaluates`, `supports`, `contradicts`, `regression-observed`. |
| Harness versions | Actual runtime/source-pin references; `executed-with`, `compared-against`. |

Use existing scoped entity naming conventions. Do not equate two components, policies or proposals merely because their names match; aliases require evidence under `knowledge/identity.py`.

Standalone R5b records retain their explicit standalone source domain. They must not acquire invented Harness task/execution nodes.

### 3. Epistemic and supersession semantics

Each assertion carries:

- Source/provenance and source timestamp.
- Actual source version.
- `claim_class`: observed fact, recorded decision, hypothesis, human correction or measured result.
- Applicable task/run/candidate/version.
- Original uncertainty and coverage limitations.
- Supporting/contradicting references.
- Supersession/correction relationships where present.

Use existing assertion `origin`, `verification`, `applicability` and derivation parents. Add closed applicability fields rather than a second assertion table.

Do not translate R5 ordinal hypothesis confidence into a fabricated calibrated probability. Preserve the original ordinal field; any existing numeric confidence must not be presented as causal certainty.

A policy denial establishes that a control held for that operation. It does not prove that the policy prevents every recurrence.

### 4. Incremental publication

Add a small per-project/source-family indexing cursor table if the stabilized producers lack a suitable cursor. It is projection bookkeeping, not a new event bus.

Protocol:

1. Select a bounded batch of source IDs/revisions under the existing operator/investigation scope.
2. Serialize only safe fields; retain exact version identities.
3. Extract outside the write transaction.
4. Before publication, revalidate selected source revisions and policy generation.
5. Publish with existing `expected_active` snapshot protection and `carry_forward=True`.
6. Advance the source-family cursor only after successful publication.
7. If cursor advancement is interrupted, repeat the batch; existing version membership makes replay harmless.
8. On snapshot/source conflict, retain the previous snapshot and report retryable incomplete coverage.

Bound batches, proposed default 100 sources, within existing byte/source limits. Never rebuild the repository graph after every operation.

Mutable source heads need an explicit revision/change cursor. Do not use timestamp-only scanning that can miss equal-time updates. For producers without a stable revision cursor, add it at their actual publication seam.

Indexing may lag source persistence. Citation rereads must identify that lag; a corrected incident cannot silently remain current in a cached answer.

Deletion, withdrawal and purge are distinct: withdrawal/supersession retains historical evidence; purged material stays excluded through existing retention checks.

### 5. Eight real query recipes

Add `harness/knowledge/reliability_queries.py`: a closed intent plus validated entity anchors, compiled into existing authorized retrieval. It must not query raw reliability tables around the Knowledge partition.

| Requested question | Evidence path and answer limit |
|---|---|
| Which decisions contributed to this failure? | Incident → hypothesis/finding → decision version. Mere referenced decisions are labeled contextual, not causal. |
| Have other agents encountered a similar problem? | Incident/category/group suggestion → related incidents → producing agents, within permitted visibility. |
| Which tests detect this category? | Category/incident → measured detecting test or regression-test proposal; distinguish demonstrated detection from proposed coverage. |
| Which policies prevent recurrence? | Incident → observed control/policy decision and evaluation evidence; distinguish held control from unproven preventive claim. |
| Which components may be affected? | Candidate targets/findings → component/dependency edges; label bounded impact inference and truncated traversal. |
| Has this improvement previously failed? | Candidate lineage → exact prior versions → evaluation outcomes; do not merge similarly named proposals. |
| Which Harness version introduced a regression? | Paired evaluation → baseline/candidate version → failed criterion. State “observed between these versions” unless a narrower causal/bisect result exists. |
| Which incidents remain unresolved? | Current investigation-status assertions plus unassessed R1 incidents; expose indexing lag and scope coverage. |

Preserve `QueryLimits`: at most 40 assertions, two hops, 200 examined edges and 32 KiB. Multi-step recipes may use a bounded aggregate budget; they must not reset limits per hop to evade bounds.

Unknown/no permitted evidence is a valid result with a reason—not a fabricated answer.

### 6. Scope and cross-agent sharing

Existing `_partition()` admits project-visible and matching-task sources; `task_id=None` does **not** mean all task-private sources. Preserve this behavior.

- Task-bound runtime facts remain task-visible.
- Native queries use `derive_scope()` unchanged.
- Operator searches can select authorized tasks explicitly; aggregate results retain their source scopes.
- Cross-task reusable knowledge uses the existing lesson/export/grant mechanism or already project-visible approved material. Do not silently widen incident visibility.
- Fresh/comparison review remains packet-local and retains current clean-review refusals.
- Assignment context may retrieve relevant reliability assertions through the existing query path; it grants no new tool or write authority.

### 7. Existing Knowledge Explorer

Extend current Knowledge UI and API with:

- Eight query intent buttons and explicit entity selection.
- Connected entity/result list using actual returned edges.
- Incident, decision-version, proposal and evaluation links.
- Fact/hypothesis/decision/correction/superseded labels.
- “Explain” and evidence actions using existing bundle/assertion references.
- Index refresh action with bounded progress and coverage reporting.
- Change-impact view showing dependency paths and unknown dynamic edges.

Keep current free-text query behavior. New request fields are additive and strictly validated. GET remains read-only; explicit refresh uses existing trusted POST protection.

Prevent task/snapshot changes from rendering delayed results into a different view. Verify keyboard focus, expansion state, wrapping and desktop/narrow alignment.

INFRASTRUCTURE_CONSTRAINTS

An additive cursor migration may be required; no new service, vector database, provider call or subscription. Startup remains schema-only. Refresh is explicit and bounded, never GET-triggered.

Use existing locked CI setup and supported SQLite requirements. Existing inspected CI/browser workflows supply Ruff, pytest, build and sandboxed Chrome; do not invent runner timeout or deployment flag values.

CORE_SCOPE: Safe source adapters, extraction/freshness dispatch, incremental refresh and bounded query recipes.

SUPPORT_SCOPE: Knowledge Explorer, API compatibility, migration/replay/security/browser fixtures and documentation.

INTEGRATION_POINTS: Existing Knowledge snapshots/evidence/FTS/graph/receipts; current R1/R2 records; stabilized R3/R5/R6/R7 interfaces.

EDGE_CASES: Corrected hypotheses, retired proposal, purged source, stale citation, interrupted batch, concurrent refresh, equal-time revisions, missing future adapter, standalone provenance, task-private related incident, unavailable clean-review scope.

RISKS: Causal overstatement, scope leakage through graph expansion or counts, stale status answers, duplicated indexing, accidental import of raw evidence.

WRITE_OWNERSHIP

CORE_FILES:
- New `harness/knowledge/reliability_sources.py`, `reliability_queries.py`, bounded refresh helper.
- Minimal `adapters.py`, `extract.py`, `query.py`, `integration.py` changes.
- Additive cursor migration if needed.
- Source publication revision hooks only where stabilized producers require them.

SUPPORT_FILES:
- `harness/dashboard/knowledge_view.py`
- `harness/dashboard/static/knowledge.js` and scoped styles.
- `harness/cli_knowledge.py` additive intent/anchor handling.
- Knowledge reliability integration/security/browser tests.

SHARED_FILES: Public request/result contracts and focused tests; Engineer then Builder.

PARALLEL_ASSESSMENT

BUILDER_EARLY_WORK: none.
BUILDER_DEFERRED_WORK: UI and integration tests after adapter/query contracts stabilize.
INTERFACE_STABILITY: New source kinds, query intents and coverage fields.
SHARED_STATE_COMMANDS: Migration and broad browser verification sequenced.
PARALLEL_SAFE: NO.
REASON: Builder consumes the new source/query interfaces.

TEST_PLAN

- T1: Real R1 denial → refresh → cited incident/agent/policy query.
- T2: R5 correction → new source version → current answer changes; old snapshot remains explicitly historical.
- T3: R6 candidate and R7 comparison → prior-failure/version-regression query with exact lineage.
- T4: Every requested entity type and all eight query recipes have actual source-backed results.
- T5: Crash between snapshot publication and cursor update replays without duplicate assertions; concurrent publication fails safely.
- T6: Foreign project/task, purged source and packet-excluded evidence remain absent from results, counts, traversal and explanations.
- T7: Unavailable adapters/measurements/dynamic dependencies yield explicit gaps.
- T8: Assignment retrieval remains context-only; clean-review and execution authority tests stay unchanged.
- T9: Browser tests exercise refresh, intent queries, evidence navigation, task switching and visual layout.

VERIFICATION_COMMANDS

Register focused Knowledge reliability tests after implementation; retain existing suites including `test_knowledge_query.py`, `test_knowledge_flow.py`, `test_knowledge_lessons.py`, `test_knowledge_dashboard.py` and relevant browser tests.

Preserve CI commands: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest -m "not browser"`, `uv run pytest -m browser`, `uv build`.

No tests were executed for this plan.

EVIDENCE_PLAN

Bind evidence to actual source adapters, producer contracts, Knowledge policy/query/store/retention, migrations, dashboard consumers, fixtures, manifests and CI recipes. Future producer integration is complete only when exercised through the producer’s actual publication path; mocked tables do not establish integration.

RUNTIME_PREREQUISITES

Supported Python/SQLite, locked dependencies, disposable local state and sandboxed Chrome for UI. No external database, native provider authentication or separate Knowledge mission acceptance prerequisite.

ACCEPTANCE_CRITERIA

- R9-01: All requested entities and relationships use the existing Knowledge store.
- R9-02: All eight queries return cited results or precise scoped coverage gaps.
- R9-03: Facts, hypotheses, decisions, corrections and superseded evidence remain distinguishable.
- R9-04: Incremental publication is bounded, replay-safe and source-revision aware.
- R9-05: Existing scope, purge, review and citation-freshness restrictions hold.
- R9-06: Cross-agent context sharing adds no execution authority.
- R9-07: Knowledge Explorer performs real queries, refresh and evidence navigation.
- R9-08: Current R1/R2 and completed predecessor adapters are integrated through actual producer paths.
- R9-09: No second memory store, silent visibility widening or blanket Knowledge gate exists.
- R9-10: Compatibility and functional/visual browser checks pass on the stabilized candidate.

Inspection hashes, to revalidate before implementation/admission:

- `knowledge/adapters.py`: `e7606b33043efa0726966453d56dd1a8f21603b9a283f3789639d9428dea7dec`
- `knowledge/store.py`: `09ea02277d41c756cc973578c29fc57b8ae4358d8c0cf8bc7c1610e56595bec6`
- `knowledge/query.py`: `81227c7def702c1c85792c4727ad2fbc2fe0044d3f255e4593070c7cae66480d`
- `knowledge/policy.py`: `d52cc48b3b55efb09dee1126998da9d6ac2adbd857413ac3ea5fb3cbe111b743`
- `knowledge/integration.py`: `d5a4985759dc3b56de2d92799ac96ce5efd50bf61372bedd789ede9d48916341`

All paths above are under `harness/`. These are pre-implementation inspection bindings, not passing evidence or authorization to consume moving source without revalidation.
