PSEUDOCODE

## Source baseline and ownership (CORE)
purpose: Replace the obsolete nullable-Harness-identity assumption with the actual R5a integration.

Observed:
- `0023_incident_catalog.sql`, `catalog.py`, `grouping.py`, `event_sources.py`, `source_queries.py`, `incident_scope.py` and both `al_incident_sources.py` copies match the corrected Architect bindings.
- `investigations.py` has moved to `d784e75ea57eaead75d63b0328c8e51f7a0b3deccbfa83309e26214068ee7c5f`; this report uses its current functions.
- The Architect addendum matches `f593e03f0de9c2e0e1801ff82155ca85118a1fde512f7d42469105cbc776303e`.

Engineer owns the partition, exporter, collector, source projection, typed-refusal producer and service dispatch. Builder follows interface stabilization for UI, integration fixtures, parity and verification. This is prospective design, not R5b writer admission or R5a verification evidence.

## New migration: standalone partition (CORE)
purpose: Preserve all settled `0023` Harness constraints while adding standalone provenance behind the same service.

signatures: Add the next migration, presently `0024_standalone_incidents.sql`; do not edit `0023`.

state changes:
```
reliability_repositories
  repository_id PK
  identity UNIQUE                   # canonical root/git_dir/common_dir, server-derived

standalone_source_runs
  source_run_id PK
  repository_id FK
  platform CHECK codex|claude
  session_id, al_run_id
  pin_identity, protected_binding_identity
  UNIQUE(repository_id, platform, session_id, al_run_id)
  UNIQUE(source_run_id, repository_id)

standalone_exports
  export_id PK
  source_run_id FK
  method_version, snapshot_id
  retained_manifest_id, manifest_digest
  created_at
  UNIQUE(export_id, source_run_id)

standalone_source_facts
  source_run_id FK
  source_kind CHECK al_test_result|al_review_finding|contract_rejection
  source_record_id, source_revision
  immutable_fact                   # canonical closed sanitized document
  PRIMARY KEY(source_run_id, source_kind, source_record_id, source_revision)

standalone_incident_catalog
  incident_id PK CHECK reserved standalone prefix
  source_run_id, source_kind, source_record_id, source_revision
  schema_version CHECK 1
  category, classification_version CHECK 1
  created_at, symptom_code, provenance
  UNIQUE(source_run_id, source_kind, source_record_id, source_revision)
  composite FK → standalone_source_facts

standalone_export_items
  export_id, source_run_id
  source_kind, source_record_id, source_revision
  assessment                       # availability/capture basis, not replacement fact
  composite FK → standalone_exports
  composite FK → standalone_source_facts
  PRIMARY KEY(export_id, source_kind, source_record_id, source_revision)

standalone_collection_requests
  repository_id, request_id
  normalized_request, export_id, receipt
  PRIMARY KEY(repository_id, request_id)

standalone_export_receipts
  export_id PK/FK
  receipt_id UNIQUE, committed_at, receipt
```

Constraints:
1. These tables have **no Harness task/execution/epoch/approval columns**.
2. An existing source-run tuple must match its pin and protected binding exactly. A changed pin is a conflict, not an update.
3. Use `sal-<32hex>` incident IDs. Reject collisions with existing Harness/legacy IDs during insertion; new Harness insertion must reject the reserved prefix.
4. Add insert guards tying source kind/category/code/provenance to the supported standalone projection. Do not reuse `_insert()` or `incident_event_sources`.
5. Facts, exports, receipts and request results are append-only; add no-update/no-delete triggers.
6. Store nonincident terminal facts too. Otherwise a later export changing a previously failed terminal entry to PASS could evade conflict detection by omitting it.
7. Migration is schema-only. No historical scan, copy, launcher invocation or source enrollment.

## `catalog.py`, `source_queries.py`, `dashboard/incident_scope.py` (CORE)
purpose: One catalog facade with explicit provenance-domain dispatch.

signatures:
- Preserve existing `detail`, `list_incidents`, `scope_filter` and old task selectors.
- Add internal `resolve_selection(conn, selected, configured_root)` returning a validated domain descriptor.
- Add standalone insertion only as a private collector service.

selectors:
```
{"task": TASK}
{"task": TASK, "execution": EXECUTION, "epoch": EPOCH}
{"domain": "standalone_al", "source_run": SERVER_ENROLLED_ID}
{"domain": "repository"}             # configured server repository, never request path
```

control flow:
1. Existing task selectors retain their exact validation and response contracts.
2. Standalone selection resolves an enrolled source run and requires its repository identity to match the configured repository.
3. Repository selection is explicit. Resolve eligible Harness scopes through existing immutable handoff ownership checks and standalone scopes through enrollment.
4. Normalize new mixed projections with `domain` and standalone provenance. Harness identity fields are null only in this **presentation projection**, never inserted as null into Harness tables.
5. Mixed listing combines:
   - existing catalog;
   - legacy R1 anti-join branch;
   - standalone partition.
6. Apply filters and descending `(created_at, incident_id)` ordering after the union. Preserve existing incident IDs/timestamps; collection never moves an existing fact.
7. Extend cursor parsing for the disjoint standalone ID grammar. Reject a cursor incompatible with its selector; UI must invalidate cursors when selection changes.
8. `source_relation` dispatches standalone IDs to retained export/source facts. It never joins them to `execution_events`.
9. Crosslinks carry actual domain/source/export identities. Standalone detail provides no fabricated execution trace.
10. GET remains read-only and does not open source files, repair retention, enroll runs or project legacy rows.

## Investigations and mixed groups (CORE)
purpose: Reuse interpretation rules without weakening domain integrity.

signatures:
- Preserve `investigations.current/history/revise`.
- Preserve `grouping.get/suggest/decide/decisions`.
- Resolve a fixed persistence adapter from validated domain before constructing SQL; never accept table names from callers.

state changes:
- Add standalone investigation revision/head tables mirroring current revision/request semantics, with FKs to `standalone_incident_catalog`.
- Enforce parent revision ownership, consecutive revisions and head-to-own-revision binding.
- Add repository-group suggestion/member/decision tables for standalone and explicitly mixed repository groups.
- Repository-group membership has exactly one of:
  ```
  harness_incident_id FK reliability_incident_catalog
  standalone_incident_id FK standalone_incident_catalog
  ```
  with a CHECK requiring exactly one, plus uniqueness per group/member.
- Keep existing Harness investigation/group tables unchanged.

control flow:
1. Reuse `validate_changes`, `references`, `rationale`, `_initial` and existing decision validation. Their catalog lookup resolves the selected domain.
2. A standalone selector limits references to that source run. An explicit repository selector permits cross-domain evidence within the configured repository.
3. Validate bounded cross-domain membership and repository ownership before the transaction; recheck immutable IDs/window inside it.
4. Preserve exact normalized request replay before revision comparison.
5. Group safe keys use repository/selection identity and existing closed symptom-family rules. Never use source prose, raw diagnostics or newly derived secret hashes.
6. Preserve member metadata differences: standalone platform/run/pin versus actual Harness task/execution/epoch.
7. Split covers every member once; supersession requires a real group in the same selected scope.
8. Existing Knowledge component evidence remains the sole verified-component mechanism.
9. `CONTRACT_REJECTED` may establish that the specific typed check rejected input. It does not establish absence of earlier effects or process settlement.
10. Existing Harness-only verdict/projection actions reject standalone selectors rather than inventing targets.

## Protected observational exporter (CORE)
paths:
- `al/installer/runtime_run.py`
- `runtime_pin_boot.py`, `runtime_pin_transaction.py`
- new `runtime_incident_control.py`
- both platforms: new `al_incident_export.py`, `al_incident_snapshot.py`
- public command registration/help and protected closure manifests

purpose: Export through the selected immutable runtime without normal command mutation or terminal-state rejection.

signatures:
```
incident capabilities
incident export --export-id <server-issued-id>
incident page --export-id <id> --index <bounded-integer>
```

control flow:
1. Collector selects configured repository, validated `codex|claude` and existing session. `managed_launcher()` discovery alone is insufficient: authenticate its actual launcher/snapshot/interpreter closure using the existing protected-launcher qualification pattern.
2. Construct only `al_argv(..., existing_run=True)` with the fixed methods above. No request-selected executable, root, argv, environment, recovery descriptor or uploaded payload.
3. Require source-qualified exporter capability in the selected pin before invoking it. Unsupported old pins refuse; never upgrade, create a run or read raw state as a fallback.
4. Add an explicit observational branch in the pin bootstrap **before ordinary `al_state.main()` dispatch**. Existing normal dispatch rejects some terminal states and reaches mutation-capable command paths.
5. Retain protected subject, ACTIVE binding, successor-authority fence, pin membership/import closure, interpreter and initialization validation.
6. Do not use `al_store.locked`: it currently creates directories and opens the lock with `"w"`. Add an observational lock using the existing lock inode, no creation/truncation, descriptor identity checks and flock. Missing/aliased lock refuses.
7. Under that lock, read one bounded ordinary state buffer from the protected `state_path(transaction)` and validate its run/session/runtime-origin/repository binding. Reject any `integration` or Harness binding.
8. Validate supported snapshot structure and selected historical references using confined readers. This is recorded-state observation, not a new approval/current-candidate verification.
9. Do not call `al_store.save`, `prepare_results`, ledger reassessment, current candidate hashing, recovery activation or ordinary command handlers.
10. Freeze the source selection in memory. No raw state buffer or raw-state digest is retained/exported.
11. `page` resolves only a previously completed export bound to this selected run/pin. It does not advance through changing ledger state.
12. Terminal AL statuses are accepted by this observational method; the independently protected run binding must still be valid.

qualification:
- Add a dedicated fixed collector/exporter channel; do not fabricate a Harness `Attempt` or execution ID.
- Bind method, nonce, selected subject, pin, interpreter and export ID at launcher and pin stages.
- Main process may read approved protected inputs and write only its derived export directory. Original state/artifacts, approvals, ledger and authority storage remain nonwritable.
- Reuse source-qualified no-fork containment and bounded helper patterns, with an exporter-specific profile. Existing `control_boundary.prepare(operation, ...)` is not directly callable without a Harness operation; add a narrow standalone profile constructor rather than a fake operation.
- Permit only the fixed repository-identity Git helper needed by protected selection. Use authenticated absolute Git, existing sanitized configuration/environment qualification and no-fork helper containment.
- No general Git grammar, native provider launch or arbitrary Python helper is added.
- Require normal method completion, exact observed main exit and closure of every registered helper. Group disappearance/PID absence alone never qualifies completion.
- Platforms lacking the actual tested profile refuse explicitly. Both AL distributions must work on each genuinely supported host profile.

## Snapshot projection and immutable pages (CORE)
purpose: Export truthful sanitized evidence, including historical records without completion receipts.

signatures:
`project_snapshot(snapshot, context)`, `read_confined_reference(...)`, `publish_export(...)`.

bounds:
- State: existing protected bound, at most 16 MiB.
- Accepted artifact: at most 4 MiB.
- 100 records/page, 256 KiB/page, 64 pages, 16 MiB/export.
- Fixed bounded nesting, reference count and source-record count.
- Oversize data refuses or produces a closed omission; never truncate into apparent valid evidence.

control flow:
1. Confined reference reads derive locations from the selected runtime artifact directory or exact protected initialization reference. Verify kind/ID/digest, ordinary single-link file, no symlink parents, descriptor stability and exact returned bytes.
2. Existing `al_artifacts.read()` alone is insufficient confinement. Do not accept its path field as authority.
3. Ledger:
   - require actual terminal status and recorded end;
   - export closed status/counts/purpose and runtime-generated evidence ID;
   - omit running entries and unsupported/empty-scope preflights;
   - label `capture_basis="recorded_terminal_fact_at_export"`;
   - never claim an existing at-completion standalone receipt.
4. Review:
   - select actual accepted references from current saved review slots and `review_history`;
   - verify retained report bytes and recorded stage/review binding;
   - retain stable accepted-report/finding position identity;
   - validate original finding IDs internally, but do not export unrestricted finding identifiers, titles, locations or prose;
   - export closed severity/category facts, with `capture_basis="retained_accepted_artifact"`.
5. Stable identities:
   - ledger: evidence ID + fixed terminal revision;
   - review: recorded review occurrence + stage + immutable finding slot;
   - typed refusal: protected occurrence ID.
   Changed facts under the same identity/revision conflict. A content hash never silently creates a new revision.
6. Include terminal nonfailures in source-fact inventory; create incidents only for eligible failures/findings.
7. Export recorded reconciliation only as a separately identified observation with actual retained provenance. Do not reconstruct process closure.
8. Separate immutable fact from export assessment:
   ```
   fact: recorded outcome/source identity
   assessment: available|missing|altered|unsupported,
               capture_basis, current_validity_not_recomputed
   ```
9. Missing/altered accepted review content cannot become a validated finding. Report closed omissions without copying source error text.
10. Manifest binds actual repository/platform/session/run/pin, method, generated snapshot ID, authorized package/reference identities, ordered pages/counts/digests and coverage.
11. Snapshot/export hashes cover sanitized export bytes only. Existing approved artifact integrity references remain references; no logs/env/transcript hashes become grouping keys.
12. Publish pages then manifest into a private derived location under protected export storage. Fsync files, atomically publish the completed directory, fsync parent.
13. Same export ID returns exactly its frozen bytes; different binding/content is conflict. Never regenerate it against newer state.

## Dedicated standalone typed-refusal producer (CORE)
paths: both `al_state.py` dispatch boundaries; new `al_incident_rejections.py`; protected diagnostic storage helper.

purpose: Record actual typed rejection without persisting a dirty failed-command state.

control flow:
1. At protected dispatch entry, retain the authenticated run binding and a runtime-generated invocation/occurrence ID.
2. Catch the actual `al_agent_contracts.ContractError` before generic error normalization/string handling.
3. Publish a separate immutable diagnostic receipt—not `al_store.save` and not an update to the command’s in-memory state.
4. Derive only:
   ```
   occurrence_id
   actual run/session/pin binding
   fixed typed boundary
   recognized family or null
   fixed INVALID_AGENT_CONTRACT code
   schema-owned root field pointer or "/"
   observed time
   effects_before_refusal="not_established"
   ```
5. Never copy exception text, rejected input, nested field names, submitted values or their hashes. Do not copy `feedback.state_changed=False` as an established operational fact.
6. Write under the protected run’s separate diagnostic outbox with exclusive immutable publication and fsync. Bind one receipt per occurrence; conflicting reuse refuses.
7. If origin validation was unavailable, emit no authenticated incident receipt. Report diagnostic capture unavailable without manufacturing history.
8. Preserve the original rejection. Capture failure must not turn it into successful command completion.
9. Exporter reads these receipts through the confined reader. Generic exit codes, `SystemExit` prose and arbitrary failures are not typed-rejection sources.
10. This adds no retrospective receipts to older runs and no execution authority.

## Collector and retention (CORE)
paths: new `standalone_collection.py`, `export_storage.py`, `export_execution.py`; dashboard action wiring.

signatures:
```
preview_collection(configured_repository, platform, session, request_id)
collect_preview(configured_repository, preview_id, request_id)
ingest_retained_export(conn, prepared_export, normalized_request)
```

control flow:
1. Preserve existing same-origin/header/body-cap boundary. Preview accepts only platform/session/request ID; collect accepts server-issued preview/request IDs.
2. Before launching, durably create a private request journal binding normalized selection, generated export ID and invocation ownership. Request replay cannot select a new run/export.
3. Run capabilities/export/pages sequentially with bounded streaming output, sanitized fixed errors and registered helper ownership. No SQLite transaction spans a child invocation.
4. Validate exact subject/pin/method, page inventory, digests/sizes/counts, unique source keys and one snapshot binding.
5. Retain the **complete** validated sanitized export in Harness-owned private storage before any catalog/source-run insertion.
6. Publish a durable preview receipt only after retained bytes and qualified invocation completion are established.
7. Preview creates no catalog enrollment. Its candidate source identity is not yet an enrolled source selector.
8. Collection verifies the retained preview and exact bytes without reopening original AL sources.
9. In one `BEGIN IMMEDIATE`:
   ```
   check request replay; changed normalized reuse => conflict
   resolve/create repository and actual source-run tuple
   check export replay; changed binding/digest => conflict
   for every source fact:
       absent => insert
       identical => reuse
       changed immutable identity/revision => abort
       eligible incident absent => insert disjoint standalone ID
   insert export/items/assessments/canonical receipt/request alias
   commit
   ```
10. Any conflict rolls back all enrollment/fact/incident/receipt changes.
11. A new request referencing the same exact export aliases the canonical receipt. A newer export repeating facts reuses those facts/incidents.
12. Source disappearance after complete retention does not prevent collection or readback.

crash behavior:
- Before complete retention: no catalog enrollment.
- Retained export before commit: reusable unreferenced bytes.
- Commit before response: exact replay returns stored receipt.
- Journal with issued launch possibility but uncertain closure remains pending/unknown; do not respawn automatically or treat a found manifest as proof of process settlement.
- Exact fenced no-start may release an unlaunched invocation. PID/group absence cannot.
- No raw stderr, native transcript or arbitrary command text is persisted in request/process journals.

## Dashboard and existing projection (CORE / SUPPORT)
purpose: Expose the integrated domain without changing historical behavior.

control flow:
1. Extend current reliability routes, not a separate dashboard.
2. Add enrolled-source listing, preview and collect actions; configured repository remains server-selected.
3. Show actual standalone provenance, capture basis, retained evidence, omissions and availability separately.
4. Preserve current old R1 routes/shapes, task selectors and interpretation attribution.
5. Mixed groups may cross domains only under the explicit repository selector.
6. Keep `legacy_projection.pending/project_legacy_batch` unchanged: bounded unindexed-row selection by rowid, revision checkpoint and request replay. No timestamp watermark or permanent-complete claim.
7. Late old-pin R1 rows remain visible immediately through the read-only anti-join.
8. Preserve UI request fencing, contiguous pagination, focus and independent list/detail/history/action state.
9. Keep the actual R7 evaluation-result adapter/schema as a **named future obligation**. No dummy evaluation FK or claimed producer.

TESTS_NEEDED
- **R5b-01/09:** Both-platform real protected standalone failed-check and accepted-review export → retained preview → collection → unified detail. Test terminal statuses and unchanged state/approval/ledger/source-history bytes.
- **R5b-02:** Unsupported pin, missing binding/lock, foreign repository/worktree/run/pin, altered launcher, path/upload/argv fields, Harness-bound run and direct native collection attempts refuse without upgrade or creation.
- **R5b-03:** Exact-buffer confinement, symlink/hardlink/FIFO attacks, changing state/reference bytes, malformed/duplicate/mixed pages, size/count bounds and secret canaries in argv/env/logs/prose/finding IDs/rejected fields.
- **Typed refusal:** Actual `ContractError` after in-memory mutation and after an earlier publication records only the independent bounded refusal; no dirty-state save and no claim that partial effects were absent. Generic errors create no typed receipt.
- **R5b-04/05:** Every retention/commit/response crash window; request/export/source-key replay and conflict; concurrent collections; terminal FAIL→PASS conflict; genuine new source occurrence preserves history.
- **Closure:** Real qualified main/helper execution; timeout, interruption, parent loss and unknown journal state never count as successful collection.
- **R5b-06:** Delete original source after complete retention; collection/detail remain available from retained evidence with truthful availability.
- **Domain integrity:** Real FKs and direct invalid inserts reject mixed identities, wrong source relation, cross-run revision/head, foreign group member and changed immutable fact. Existing Harness APIs and constructors remain unchanged.
- **Interpretation/grouping:** Shared validation, exact request replay, revision conflicts, cross-domain repository evidence, split/supersede scope, and existing Knowledge component provenance.
- **R5b-07:** Empty migration/startup/GET performs no source scan/copy/repair; late backdated legacy rows enroll on a new bounded request; projection between pages creates no duplicates.
- **R5b-08:** Chrome desktop/narrow source selection, preview, collection, conflicts, provenance crosslinks, history/groups, focus, wrapping, overflow and stale-response handling.

EARLY_SUPPORT_SAFE
- none

DEFERRED_SUPPORT
- Builder UI and public integration fixtures wait for domain selector, normalized projection and preview/collection contracts.
- Crash/FK tests wait for final partition and journal transitions.
- Packaging/parity verification waits for protected observational dispatch, refusal producer and source-closure updates.
- Revalidate moving R5a interfaces before R5b writer admission; do not substitute this source observation for final verification.

OPEN_QUESTIONS
- []
