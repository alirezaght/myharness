R5b should be **COMPLEX, Engineer + Builder, Designer required**. The new hard part is establishing provenance across a standalone AL store and the Harness database while preserving immutable exports, replay safety and source retention. R5a remains STANDARD.

The earlier migration recommendation also needs correction: startup should create schema only, without scanning or copying historical R1 incidents.

### Trusted selection and exporter

Use the dashboard’s configured repository as the root of selection. The operator supplies only a validated platform and existing AL session identifier—not a filesystem path, uploaded JSON or arbitrary command.

Resolve the run through the protected launcher:

- `al/installer/runtime_run.py:53` binds repository/platform/session and supports `existing_run`.
- `runtime_pin_transaction.py:34,117,130` resolves the protected subject, runtime directory and state path.
- `runtime_run.py:64,89` already refuses inappropriate existing-run use and missing active protected binding.
- Installer registry records identify installations; they are **not** proof that a particular run or export is authoritative.

Add a qualified public command, proposed:

`alctl run --repository <configured-root> --platform <selected-platform> --session <selected-session> --existing-run -- incident export ...`

The collector constructs argv itself. It does not run commands from request content.

Exporter qualification belongs to the selected pinned runtime. Unsupported older pins return a capability limitation; no silent upgrade, direct state scraping fallback or new run creation.

### Immutable closed export

The exporter reads the selected run through its existing guarded state/artifact readers and publishes a closed immutable receipt containing:

- Schema/export-method version.
- Actual repository identity, platform, AL session and run ID.
- Protected runtime-pin/source identity.
- Effective specification/package revision.
- Source snapshot/checkpoint binding.
- Export ID and bounded page manifest.
- Incident records with stable source keys.
- Source classification and provenance.
- Closed sanitized facts and required evidence references.
- Explicit coverage and omissions.

Standalone context is:

`domain:"standalone_al", repository_id, platform, session_id, al_run_id`

There are **no fabricated Harness task, execution, epoch or approval fields**.

Derive incident records from accepted ledger/review/typed failure records. Do not export arbitrary artifact bodies. Existing `al_artifacts.read()` verifies referenced bytes against their hashes, but its path-bearing references must not be accepted blindly by the Harness collector.

The exporter returns only bounded safe evidence projections and integrity references. Raw logs, source paths outside the authorized run, private transcripts and arbitrary diagnostic attachments remain excluded.

### Operator collector protocol

Add explicit same-origin dashboard preview/collect actions, using existing Host/Origin/body validation. No native tool receives this collection capability.

Designer must close this sequence:

1. Resolve the configured repository and selected protected run.
2. Invoke its qualified exporter through owned execution.
3. Validate export identity, method, size, page membership and all digests.
4. Retain the complete bounded sanitized export in Harness-owned private storage.
5. Open one database transaction.
6. Check export/source identities for exact replay or conflict.
7. Insert standalone source enrollment, catalog rows and evidence references.
8. Commit an ingestion receipt.
9. Return that receipt to the dashboard.

Do not require a distributed transaction with AL. The source export is immutable before Harness ingestion.

Crash rules:

- Before retained export publication: no enrollment or incidents.
- After retained export but before DB commit: harmless unreferenced immutable bytes; retry may reuse them.
- After DB commit but before response: identical retry returns the existing receipt.
- Same export identity with different bytes: hard conflict, no partial ingestion.
- Source disappearance after collection does not invalidate the retained sanitized evidence; expose source availability separately.
- A changed run snapshot requires a new export, never reinterpretation of the old one.

Freeze bounded pages under one export manifest. Reject mixed pages or incomplete manifests before publication. Do not repeatedly read moving run state while advancing a collection cursor.

### Catalog domain and durable integrity

Extend the catalog design with explicit source-domain enrollment:

- `reliability_source_runs(source_run_id, domain, repository_id, platform, session_id, al_run_id, pin_ref)`
- `reliability_exports(export_id, source_run_id, snapshot_ref, manifest_ref, method_version)`
- `reliability_export_receipts(export_id UNIQUE, request_id UNIQUE, committed_at)`
- Standalone source relation keyed by `(source_run_id, source_record_kind, source_record_id, source_revision)`.

Harness incident rows retain their actual Harness bindings. Standalone rows reference `source_run_id`; constraints forbid simultaneously pretending they are Harness-bound.

A newer export may repeat prior source records. Exact repeats reuse the canonical incident. Changed content under an immutable source revision is a conflict. Legitimate revised findings create new evidence revisions, preserving prior versions and correction history.

Hashes establish integrity of already authorized sanitized content; they are not credentials and must not be calculated from raw secrets for grouping.

### Historical R1 indexing without startup scans

Schema migration creates empty catalog/projection structures and indexes only.

Provide a bounded explicit projection action:

- Scope: configured repository and selected task/execution.
- Fixed batch limit, proposed maximum 100 rows.
- Stable keyset cursor over existing R1 incident identity/order.
- In one transaction, insert catalog/source relations for that batch and advance its checkpoint.
- Preserve the exact existing incident IDs.
- Repeated batches are idempotent.
- Never create an observation, diagnosis, hypothesis or runtime event.

This is an operator-owned projection of already persisted facts. It is not a historical runtime writer and does not change execution state.

New R1 incidents continue writing catalog/source rows atomically in their existing current-epoch transaction.

For old records:

- Existing R1 APIs remain immediately usable.
- Unified reads include a read-only legacy branch for R1 rows not yet cataloged, using `NOT EXISTS` to avoid duplicates.
- Expose `catalog_state:"legacy_unindexed"` and projection progress.
- Investigation/candidate actions require the explicit bounded enrollment/projection first.
- GET never performs indexing.
- Pagination uses a common deterministic ordering across catalog and legacy branches.

### Dashboard integration

Add a repository-level standalone source selector alongside existing execution-scoped incidents.

Standalone detail prominently shows repository/platform/run provenance. It must not display a borrowed task or execution label. Related-incident queries can cross domains within the same repository, but retain separate provenance and ordinary hypothesis/grouping rules.

Show export coverage, collection state, source availability, conflicts and unindexed R1 coverage. Human corrections use the existing operator action boundary and append interpretation revisions; they do not rewrite exported facts.

### Designer deliverables

Require pseudocode for:

1. Protected run selection and exporter capability verification.
2. Snapshot/page freeze and safe artifact resolution.
3. Retain-before-commit ordering and crash recovery.
4. Export/source-key replay and conflict rules.
5. Domain constraints and scoped query authorization.
6. Bounded legacy projection plus duplicate-free mixed pagination.

Do not add native permissions, approval bypasses, arbitrary upload APIs or a background collector.

### Ownership

Engineer:

- Both AL platforms’ public exporter, closed contract and command registration.
- Minimal protected launcher capability plumbing if required.
- `harness/reliability/standalone_collection.py`, export validation/storage and source adapters.
- Catalog domain migration and bounded R1 projection.
- Core scoped read/action handlers.

Builder, after interfaces stabilize:

- Dashboard source selection and coverage presentation.
- Public exporter→collector integration fixtures.
- Crash/replay/conflict, migration and pagination tests.
- Both-platform parity/package verification and Chrome evidence.

### Acceptance criteria

- A real protected standalone run exports accepted failures and appears in the existing incident dashboard.
- No Harness identity is fabricated; foreign repository/run bindings and arbitrary paths/uploads are refused.
- An unsupported pinned exporter remains unsupported without upgrade or fallback scraping.
- Export pages remain bound to one immutable snapshot.
- Collection is idempotent across all crash boundaries; conflicting identities never overwrite facts.
- Retained sanitized evidence remains readable after original source removal.
- Cross-domain grouping preserves provenance and does not assert common cause.
- Startup migrations do no historical row scan/copy.
- Bounded projection preserves old IDs and resumes safely; mixed reads show each incident once.
- GET remains read-only, and stale native writers gain no historical mutation authority.
- Browser checks cover source switching, conflicts, progress, legacy coverage and desktop/narrow visual behavior.

No services, edits or tests were run for this brief.
