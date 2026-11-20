CODE_FINDINGS

**R2-FRESH-001 — HIGH — Unvalidated semantic payload fields enter safe timeline metadata.**

Criteria: R2-10; invariant R2-I2; accepted requirement “Only source IDs, closed kinds and explicitly validated references.”

Location: `/private/tmp/al-r2-frozen-review-001/harness/reliability/timeline_store.py:82`.

`semantic_link()` chooses `payload.id`, then `delivery_id`, then `task_id` regardless of event kind. However, `/private/tmp/al-r2-frozen-review-001/harness/execution_contract.py:273` permits additional payload fields. Run events do not validate any of those reference fields; output and decision events validate different specific fields.

Concrete failure scenario: after normal run initialization, ingest a correctly bound, correctly sequenced `run.ended` event with:

```json
{"status":"RUNNING","id":"Authorization: Bearer private-canary"}
```

Existing validation and projection accept it. R2 copies the arbitrary string into `reliability_timeline_links.reference_id`; `timeline_query.py:86` returns it, and `timeline.js` renders it as a semantic reference. An extra `id` can similarly override the legitimate `task_id` or `delivery_id` of other event types. A collection-valued extra field can instead cause SQLite binding failure and reject an otherwise valid event.

**Required fix:** Select reference fields by exact event kind, using only the field validated for that kind. Run events should have no payload-derived reference. Preserve existing event compatibility rather than globally rejecting extra fields. Add public-ingestion tests covering extra `id`/`delivery_id`/`task_id` text, secret hashes and collection values, then verify new tables and list API responses contain only the intended references.

This is source-derived; I did not execute a reproduction.

CRITERIA

Paths below are relative to `/private/tmp/al-r2-frozen-review-001/`.

| Criterion | Code assessment | Proof assessment |
|---|---|---|
| R2-01 | Dispatch context, process observer and foreground/background integration provide durable parent correlation. | Relevant tests exist; current core evidence remains stale. |
| R2-02 | Distinct launch, cancellation request, timeout, exit and cleanup observations; nonzero return codes retained. | Real-process and cleanup-fault tests exist; current proof pending. |
| R2-03 | No ownership reconstruction or process replay; incomplete spans remain incomplete. | Fault/retirement coverage exists; no durable-recovery qualification claimed. |
| R2-04 | Frozen `ProcessContext` and `ContextVar` avoid mutable shared parent identity. | Concurrent-principal test exists; current proof pending. |
| R2-05 | `_publication` checks exact epoch/generation; ingestion diagnostics repeat the original fence. | Generation/retirement/diagnostic-race tests exist; current proof pending. |
| R2-06 | Event, projection, link and cursor share a transaction; identical replay returns before insertion. | Atomicity/conflicting-replay tests exist; the extra-field failure above needs coverage. |
| R2-07 | Runtime and semantic provenance remain explicit; UI disclaims acceptance and descendant-cleanup inference. | Supported by inspected code and browser evidence, subject to R2-FRESH-001. |
| R2-08 | Existing authorized Knowledge query/explain/assignment paths supply bundle references; timeline adds no retrieval capability. | Tests exist; current core evidence pending. |
| R2-09 | Schema-only migration; read-only API; absent historical trace produces unavailable correlation. Snapshot inventory explicitly includes new modules. | Compatibility tests exist. Snapshot tests model inventories, not full historical-runtime execution. |
| R2-10 | **Defective: R2-FRESH-001.** Process fields otherwise exclude argv/output/environment/error prose. | Existing canaries do not cover unvalidated semantic reference fields. |
| R2-11 | Exact scoped lookup, bounded keyset pagination, independent request tokens and scope ownership checks. | E20/E21 retained as VALID; core HTTP proof is stale. |
| R2-12 | Token, cost and native-child measurements remain unavailable; comparisons explicitly unqualified. | No invented zero values or byte-derived measurements found. |
| R2-13 | Focus/disclosure preservation, bounded history window and live detail refresh implemented. | E20 covers desktop/narrow browser behavior. Inspected both retained screenshots; no clipping, overflow or alignment defect found. |
| R2-14 | Inspected integration preserves existing dispatch, ingestion and snapshot seams, apart from the reported semantic-reference regression. | E14/E15 stale; E21 valid browser regressions. Four native skips provide no qualification. |
| R2-15 | `execution_store.py:98` creates the trace within reservation’s existing outer transaction; duplicate branch returns earlier. | Reservation/rollback/launch-failure tests exist; current core evidence pending. |

COUNTEREXAMPLE_SEARCH

- Traced simultaneous process parent/principal attribution, retirement between observation callbacks, and stale diagnostic writes.
- Examined spawn failure, post-spawn recording failure, timeout, cancellation, nonzero exit, cleanup uncertainty and duplicate terminal publication.
- Checked semantic rollback/replay and field-level validation, finding R2-FRESH-001.
- Examined authorized retrieval references, scoped detail membership, absent old tables, read-only GET and explicit snapshot closure.
- Reviewed delayed list/detail responses, scope changes, retained history, disclosure/focus restoration and live terminal-detail refresh.
- Found no additional concrete code defect warranting a finding in these inspected paths.

PENDING_FORMAL_PROOF

- This is a diagnostic assessment, not a formal v2 review.
- `evidence-status.txt` retains failures E1/E2/E4/E10/E16 honestly. E3/E5–E9/E11–E15 are stale; their PASS counts cannot qualify this candidate.
- E17–E21 are recorded VALID for JS syntax, lint, formatting and browser checks. They do not replace current core/R1/process/event/epoch/Knowledge/migration/snapshot verification.
- Repair R2-FRESH-001, obtain candidate-bound evidence, complete mandatory CI/service coverage and formal review. Native skips remain explicit gaps.
- No tests, services, database connections or writes were performed by this reviewer. Future mission phases remain in scope but were not demanded as completed by R2.
