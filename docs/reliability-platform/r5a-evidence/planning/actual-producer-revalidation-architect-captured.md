SPEC_STATUS: SPEC_READY

TASK_CLASSIFICATION: STANDARD  
IMPLEMENTER: both, sequentially  
DESIGNER_REQUIRED: NO

R5a extends existing current-epoch transactions, immutable artifacts and operator revision/replay patterns. The remaining work does not introduce a new execution-authority or cross-store protocol. R5b remains separately COMPLEX. This revalidation uses `/private/tmp/al-r4-frozen-review-001`; it is authored-core inspection, not current-final-source verification or approval.

### Necessary source corrections

**1. Policy decisions need their own source relation.**

`harness/policy/admission.py:27–55` inserts directly into `policy_decisions` and links the timeline. This is not an AL event. Denied decisions deliberately have `attempt_id=NULL`. Migration `0021_runtime_policy.sql:84–88` has eight columns, and existing bridges use positional eight-value inserts.

Add `incident_policy_sources(incident_id, policy_decision_id UNIQUE)` referencing the actual decision. At `audit()`, insert the decision, optional closed detail, catalog incident and source relation using the **caller’s existing transaction and connection**. Derive Harness task/execution/epoch from `execution`; do not fabricate an attempt, subtask or AL event.

`BUDGET_EXHAUSTED` supports generic exhaustion attribution. It currently does not prove a pool, dimension or measured quantity. If new failures supply those details, capture them explicitly at the rejecting budget check into a separate typed sidecar keyed by decision ID. Preserve the eight-column table and old inserts. Legacy missing detail stays unknown; unsupported capability refusal is not budget exhaustion.

**2. R4 currently returns contract errors but does not persist a dedicated rejection receipt.**

`agent_contracts.ContractError:49–63` constructs safe feedback. Dispatch catches it as `ValueError` and records generic `DISPATCH_REJECTED` through `observations.finish()` (`execution_operations.py:467–478`). `await_assignment():1336–1344` returns `contract_error` without a dedicated durable contract source.

Add the actual closed producer in R5a:

- `contract_rejection_receipts`: server-generated receipt ID, actual execution/epoch/generation, authenticated producer, boundary, supported contract family or null, fixed error code and allowlisted field pointer.
- `incident_contract_sources(incident_id, rejection_id UNIQUE)`.
- Optional observation reference only when that observation actually exists.

Capture after authenticated context exists. Revalidate current epoch at publication; use the existing current-epoch publication pattern. For dispatched rejection, extend the existing observation-finish transaction with the receipt/catalog insertion rather than adding a nested connection. For assignment-wait rejection, use a separate short publication after validation has unwound, with fresh epoch checks.

Never persist rejected bodies, arbitrary keys/values, exception prose or their hashes. A generic ValueError is not automatically a contract rejection. Existing R1 facts and IDs remain intact; an explicit relation can show that the operation failure and typed rejection describe the same boundary occurrence.

**3. Accepted test/review evidence requires an actual immutable producer binding.**

`al_events.prepare_commit():75–91` emits `test.result` from ledger entries. The existing payload carries status, scope, counts and validity, but no immutable accepted-entry receipt. `al_review.cmd_report():115–136` validates a report, snapshots its exact bytes and saves its reference; it does not currently emit a dedicated review-finding source event.

In both AL distributions:

- Capture an immutable terminal ledger-result receipt through the existing guarded save path, retaining actual evidence identity, verdict provenance and available log/reference bindings.
- Add a closed source-reference event for accepted review reports at the validated snapshot/save seam.
- Preserve historical event vocabulary and bytes; upgraded producers add explicit versioned source metadata/events. Old events remain lower-detail verification observations.
- Keep source classification separate from review text. A failed check proves an observed verification failure, not automatically incorrect implementation.
- Registered check purpose is intended purpose. CI/staging attribution additionally requires actual runner/environment provenance; free-form area names never supply it.

### Publication and integrity flow

`execution_worker.py:320–347` reads the bounded authoritative AL state/outbox. `execution_projection.ingest_event():239–317` already commits event, projection, timeline and cursor together.

Add a bounded preparation helper for new evidence-bearing events:

1. Validate actual outbox binding and source-reference shape.
2. Resolve references only under the server-selected AL issuing root or actual execution artifact index.
3. Read and validate exact immutable buffers once. Derive the safe incident projection from those same buffers.
4. Before commit, retain the existing epoch/generation, event binding, sequence and duplicate checks.
5. Inside `ingest_event()`’s existing transaction, insert event, catalog/source rows, projection and cursor atomically.

No subprocess, provider call, arbitrary path import or separate SQLite writer belongs inside this transaction. Prepared validation data must bind the exact event/reference bytes; it is an internal result, not caller-provided permission. Missing or changed report bytes reject acceptance. Duplicate/conflicting/stale delivery retains existing behavior.

Review incidents are attributed validated findings, not proven root causes. Keep stable report/finding identity and exact accepted report revision; later comparison/resolution is additional evidence, not deletion of the earlier finding.

### Canonical persistence and operator flow

Retain the catalog and investigation design, with these concrete relations:

- Operation source → existing R1 incident ID.
- AL event source → actual `(execution_id, epoch_id, event_id)` plus stable item identity.
- Policy source → actual policy decision ID.
- Contract source → new authenticated rejection receipt ID.
- Operator source → exact operator request/verdict revision.
- Evaluation source → later R7 result, once that producer exists.

Each source-specific insertion validates exactly one corresponding relation in the same transaction. There is no generic import endpoint.

Investigation heads/revisions reference the canonical catalog. Corrections, grouping decisions and historical operator verdicts use the existing local-dashboard POST authority and request-ID/expected-revision pattern. They do not reuse `record_execution_decision()` to write historical runtime state: that function explicitly requires active-epoch mutation authority (`execution_decisions.py:164–205`).

Preserve facts, hypotheses, ordinal confidence, controls held/failed/unknown, correction rationale and actor provenance separately. Group suggestions remain bounded and reproducible; rejection/splitting never merges or deletes incidents.

### Compatibility and delivery boundary

The later legacy-projection addendum supersedes the original migration-copy paragraph:

- Startup performs schema changes only.
- Mixed reads use identity-preserving catalog/legacy union with `NOT EXISTS`.
- Explicit operator batches select at most 100 unindexed R1 rows; checkpoint is a replay/concurrency revision, not a permanent timestamp cursor.
- Late old-pin rows remain visible immediately and can be projected by a later request.
- GET never projects; old pins remain unchanged.

R5a investigation/grouping consumers are implemented here. Actual R6 candidate consumption remains a named downstream verification obligation until R6 exists—not a reason to block R5a authoring. R7 evaluation-source integration likewise remains pending. Standalone reports retain real AL provenance; unified ingestion belongs to R5b and must not fabricate Harness bindings.

### Ownership and checks

Engineer owns catalog/source relations, source-specific publication helpers, both-platform event/report producer hooks, `ingest_event()` integration, policy/rejection capture, investigations/grouping and bounded operator projection. Builder follows with scoped API/UI, compatibility fixtures, parity/source closure and browser evidence.

Required focused cases:

- Failed real ledger check and accepted review finding create incidents without fabricated operation observations.
- Budget denial with null attempt produces a correctly linked incident; legacy positional inserts still work; absent dimension remains unknown.
- Real dispatch and assignment-wait contract rejections persist only closed safe metadata; foreign/stale callbacks cannot publish.
- Tampered/missing artifacts, conflicting replay and injected pre-commit failure leave event/catalog/cursor atomic.
- Exact-buffer replacement race cannot substitute different report contents.
- Operator correction works for historical incidents without changing runtime facts or epochs.
- Late/backdated old-pin rows, concurrent projection, exact request replay and mixed pagination preserve identities.
- Dashboard search, provenance navigation, investigation, grouping and conflicts work at desktop/narrow widths with visual and focus checks.
- Existing R1/R2 interfaces and both AL distributions remain compatible.

No new product decision is unresolved. Source bindings and required checks must be refreshed after R4 Builder finalization; no tests, services or mutations were performed for this revalidation.
