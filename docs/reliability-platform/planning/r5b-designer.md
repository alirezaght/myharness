PSEUDOCODE

## Both AL distributions: scripts/al_incident_export.py; scripts/al_state.py registration (CORE)
purpose: Export accepted standalone facts through the selected protected runtime without granting execution authority.
signatures: Add `register(sub)`, `cmd_capabilities(a, sid)`, `cmd_export(a, sid)`, `cmd_page(a, sid)`; retain existing `al_state._dispatch`, `al_store.load`, `al_store.locked`.
inputs / outputs: Fixed command, validated export ID/page index → closed capability receipt, frozen manifest or bounded sanitized page.
state changes: Publish immutable derived export artifacts only; never call `al_store.save`, change run status, create observations or update approval.
control flow:
  1. Register `incident capabilities`, `incident export --export-id ID`, and `incident page --export-id ID --index N`.
  2. Explicitly permit these observational commands on terminal runs in `_dispatch`; retain `require_dispatch()` and protected run validation.
  3. Require `_al_run_pin_context`, ACTIVE transaction, matching repository/platform/session/run and standalone state without `integration` or Harness binding.
  4. Capabilities report the selected pin’s actual export method/schema and limits. An unknown command or unsupported method remains unsupported.
  5. Export under the existing state lock. Read one validated state snapshot; derive all records and pages from that snapshot.
  6. Return a previously published export for the same export ID; never reinterpret that ID against newer state.
  7. Page reads resolve only frozen export storage. They do not traverse moving ledger state.
data flow: Protected pin identity + actual run identity → export provenance. Installer registration alone is insufficient.
failure / error behaviour: Reject Harness-bound runs on this standalone route, foreign identity, missing state, unsupported pin and malformed selectors.
edge cases: `--existing-run` prevents initialization but does not itself make every AL command read-only; exporter implementation must enforce that property.
integration points: Both `al_state.py` registrations and package closure/parity validation. Existing pins acquire no new capability automatically.

## al/installer/runtime_run.py, runtime_pin_transaction.py; Harness trusted selector (CORE)
purpose: Resolve only an existing protected run under the dashboard’s configured repository.
signatures: Preserve `run(repository, platform, session, argv, *, existing_run=False, private_session=False)`, `subject(...)`, `read_state(transaction)`; add only minimal capability plumbing if required.
inputs / outputs: Operator-selected platform/session → exporter invocation bound to configured repository.
state changes: No pin installation, upgrade, run creation or authority repair.
control flow:
  1. Validate platform against `codex|claude` and session against the existing protected identifier grammar.
  2. Obtain repository and managed launcher from trusted server configuration/current managed-launcher resolution, never request fields.
  3. Construct argv:
       `launcher run --repository ROOT --platform PLATFORM --session SESSION
        --existing-run -- incident ...`
     Set cwd to ROOT; sanitize inherited environment using existing launcher conventions.
  4. Let protected launcher validate subject, ACTIVE binding, pin membership, interpreter and state origin.
  5. Accept exporter output only from this owned invocation after successful closure and exact method/identity validation.
  6. Preserve the protected transaction/pin identity across capability, export and page calls; changed bindings require a new preview.
data flow: Collector consumes bounded stdout documents, never `show --json`, runtime state files or paths returned inside artifacts.
failure / error behaviour: Unknown exporter gives a capability limitation. No direct-state fallback, user-selected executable or silent old-pin upgrade.
edge cases: Repository identity includes the existing `repository_identity()` root/git-directory/common-directory semantics; do not equate neighboring worktrees solely by common Git directory.
integration points: `harness.execution_hosts.managed_launcher`, existing isolated launcher and owned-process utilities.

## AL export contract and safe source projection (CORE)
purpose: Freeze bounded sanitized pages with trustworthy source references.
signatures: Add `project_snapshot(st, context)`, `read_run_artifact(ref, allowed_origin)`, `publish_export(export_id, manifest, pages)`.
inputs / outputs: Validated snapshot and accepted ledger/review references → closed manifest and pages.
state changes: Immutable export directory under derived run-owned export storage.
control flow:
  1. Define method limits, for example: 100 records/page, 256 KiB/page, 64 pages, 16 MiB total, bounded evidence count per record. Reject oversize records; never truncate a record into valid-looking evidence.
  2. Manifest binds schema/method, export ID, domain, repository identity, platform/session/run, pin/source identity, effective task-package revision, snapshot reference, ordered page digests/sizes/counts, coverage and omissions.
  3. Obtain task revision through existing validated task-package lineage. Export only integrity references, not specification prose.
  4. Validate source artifact references before reading:
       approved kind;
       expected artifact ID/hash;
       derived run artifact location, or exact independently protected initialization resource;
       no symlink/hardlink/special-file alias;
       bounded stable descriptor read.
     Existing `al_artifacts.read(ref)` checks content hash but does not establish path confinement; it is insufficient alone.
  5. Derive ledger incidents from recorded terminal failures and closed fields such as evidence ID, kind, status and counts. Do not export argv, environment hashes, free-form area/note, logs or diagnostic text.
  6. Derive review incidents only from accepted retained report references and stable finding IDs; export bounded criterion/classification/provenance metadata, not arbitrary report bodies.
  7. Keep recorded outcome separate from present validity/source availability. Current source movement must not rewrite the original failure.
  8. Use stable record identity:
       ledger: `(kind, evidence_id, "terminal-v1")`;
       review: `(kind, review_id + stage + finding_id, accepted_report_ref)`.
     Existing ledger terminal records have no independent revision counter. Changed terminal facts under that identity conflict; do not invent a new revision merely by hashing changed content.
  9. Explicit accepted revisions/reconciliations, where supported, become separately bound evidence revisions. Unknown revision mechanisms are coverage omissions.
  10. Freeze all pages, then manifest. Write temporary private files, fsync files, atomically publish the completed directory, fsync its parent.
  11. Emit the manifest only after durable publication. Existing export ID with different bytes is a hard conflict.
data flow: Snapshot identity binds the sanitized selected source projection and existing protected references. Secret/raw diagnostic hashes never become grouping keys.
failure / error behaviour: Required missing/altered evidence prevents that record from being asserted as verified; expose closed omissions. Invalid export identity or inconsistent page inventory rejects the export.
edge cases: RUNNING or missing-PID ledger entries are not completed failures. No process-settlement inference from `al_ledger.assess()` liveness heuristics.
integration points: Existing task, ledger and review readers; source-specific allowlists must be revalidated against R5a’s actual exporter contracts.

## harness/reliability/standalone_collection.py, export_storage.py (CORE)
purpose: Retain a complete authorized export before enrolling it in SQLite.
signatures: Add `preview_collection(controller, selection, request_id)`, `collect_preview(controller, preview_id, request_id)`, `retain_export(...)`.
inputs / outputs: Same-origin operator selection → preview; selected retained preview → ingestion receipt.
state changes: Private retained artifacts first; enrollment/catalog transaction afterward.
control flow:
  1. Validate closed small request. Preview accepts platform/session/request ID only; collect accepts server-issued preview ID/request ID. No paths, uploaded JSON or commands.
  2. Generate an export ID and durably bind it to the sanitized preview request before exporter invocation. Exact preview replay resumes the same export ID.
  3. Invoke capabilities/export/pages sequentially through bounded owned execution. Limit stdout, stderr, time, pages and total bytes while receiving—not merely after `communicate()` has allocated everything.
  4. Verify exact manifest identity, supported method, contiguous page inventory, sizes, digests, record counts, unique source keys and every page’s export/snapshot binding.
  5. Reject incomplete or mixed pages. Never publish a partially collected export.
  6. Retain complete sanitized bytes in Harness-owned private storage outside participant writes. Publish content-addressed files and an immutable manifest with fsync/atomic rename.
  7. Publish a private preview receipt binding request, configured repository, selected source, retained manifest digest and capture provenance.
  8. Preview displays coverage and facts but creates no catalog enrollment.
  9. Collect resolves this server-owned receipt and verifies retained bytes; it does not trust client-supplied digests or reopen source paths.
  10. Open SQLite `BEGIN IMMEDIATE` only after complete retention. Run the ingestion algorithm below.
data flow: Trusted process result → validated sanitized retained export → database facts.
failure / error behaviour:
  - Export/process failure: no enrollment.
  - Retention failure: no enrollment.
  - Uncertain process closure: do not treat output as a successful preview.
  - Fixed errors exclude raw subprocess stderr.
edge cases:
  - Before publication: removable incomplete staging.
  - After retention/before commit: harmless unreferenced bytes; exact retry reuses them.
  - After commit/before response: return existing receipt.
  - Once retained, source disappearance does not prevent collection/readback of the already authorized snapshot.
integration points: No background collector and no native capability. Reuse owned lifecycle mechanisms, with bounded capture and durable collection-request identity.

## Catalog migration and standalone ingestion (CORE)
purpose: Enforce provenance domains, immutable source identity and duplicate-free collection.
signatures: Add `ingest_retained_export(conn, retained, request_id) -> Receipt`; extend R5a catalog via domain-aware source-specific constructors.
inputs / outputs: Verified retained export → canonical incidents, source revisions and receipt.
state changes: One all-or-nothing transaction.
control flow:
  1. Schema-only migration creates domain/source/export/receipt relations and indexes; no historical R1 scan or copy.
  2. Catalog constraints:
       `domain='harness'` requires actual Harness bindings and no standalone source run;
       `domain='standalone_al'` requires `source_run_id` and NULL Harness task/execution/epoch/approval.
     Each incident has exactly its typed source relation; no generic insertion endpoint.
  3. Source-run uniqueness uses repository identity, platform, session and actual AL run ID. Bind pin provenance explicitly; do not infer run identity from labels.
  4. Check request ID first:
       same normalized request and retained export → return prior receipt;
       same request ID with different binding → conflict.
  5. Check export identity:
       existing exact manifest/source/snapshot binding → return canonical receipt;
       same export ID with different bytes/binding → conflict.
     Persist request aliases separately if multiple valid request IDs reference that canonical receipt.
  6. For each `(source_run_id, source_record_kind, source_record_id, source_revision)`:
       absent → insert canonical incident/evidence relation;
       identical sanitized immutable facts → reuse;
       differing facts → abort entire ingestion.
  7. Attach each export’s coverage/availability assessment separately from immutable incident facts.
  8. Legitimate later source revisions append evidence, preserving prior versions and investigation history.
  9. Insert export, retained manifest reference and ingestion receipt; commit.
data flow: Source keys identify facts; hashes validate already authorized bytes. Related-incident matching never changes provenance.
failure / error behaviour: Any conflicting record rolls back enrollment, new incidents and receipt together. Existing facts are never overwritten.
edge cases: A newer export repeating older facts does not duplicate incidents. A source availability check is a separate operator observation; it does not invalidate retained evidence.
integration points: R5 investigation heads reference canonical catalog. R5a catalog/adapter signatures and migration state must be revalidated after implementation.

## harness/reliability/legacy_projection.py, catalog_query.py (CORE)
purpose: Explicit bounded historical R1 indexing while all old incidents remain readable.
signatures: Add `project_legacy_batch(conn, selected_scope, expected_checkpoint, request_id, limit=100)`; extend catalog list/detail adapters.
inputs / outputs: Configured-repository/task/execution scope → projection progress; read-only mixed query → one row per incident.
state changes: Catalog/source relations plus projection checkpoint only.
control flow:
  1. Validate actual task/execution ownership using existing `query.scope` semantics; historical epoch is evidence context, not runtime mutation authority.
  2. Begin immediate transaction; resolve idempotent request replay before checkpoint comparison.
  3. Select at most 100 legacy incidents after stable ascending `(created_at,incident_id)` checkpoint.
  4. For each, insert the catalog row using the exact existing incident ID, timestamp, category and classification; insert its unique operation-source relation. Exact existing rows are no-ops; differences conflict.
  5. Commit batch, checkpoint and request outcome together. No observation, hypothesis, diagnosis or execution event is created.
  6. New R1 incidents insert legacy and catalog/source rows in their existing current-epoch transaction.
  7. Mixed reads use one read-only SQLite snapshot:
       catalog projection
       `UNION ALL`
       legacy projection `WHERE NOT EXISTS`
       matching `incident_operation_sources.legacy_incident_id`.
  8. Apply common filters and descending `(created_at,incident_id)` keyset ordering outside the union. Projection preserves these keys, so indexing cannot move or duplicate a listed incident.
  9. Legacy branch reports `catalog_state='legacy_unindexed'`; interpretation/candidate writes require explicit projection first.
data flow: Cataloged legacy row and compatibility row are two projections of one identity.
failure / error behaviour: Stale checkpoints return conflict/reload; GET never calls migration-enabled `connect` or projection.
edge cases: Capture a batch high-water mark if showing finite progress; newly arriving incidents remain readable and are atomically cataloged. Repository-wide standalone listing must not reuse task-required legacy routes.
integration points: Existing R1 APIs/detail URLs remain unchanged. R5b’s schema-only migration instruction supersedes R5a’s earlier historical-copy recommendation.

## Dashboard operator actions and views (CORE / SUPPORT)
purpose: Make collection provenance and incomplete coverage visible without expanding native authority.
signatures: Extend `WRITES`, `HarnessHandler.do_POST`, focused reliability action/view adapters.
inputs / outputs: Explicit preview/collect/project requests; scoped read-only source views.
state changes: Only explicit operator POSTs.
control flow:
  1. Preserve Host/Origin/content-type/content-length checks; apply action body cap before `_body`.
  2. Derive repository scope from server configuration and database enrollment.
  3. Render standalone repository/platform/session/run provenance without borrowed Harness labels.
  4. Show retained export coverage, source availability, conflict state and legacy projection progress separately.
  5. Fence delayed responses by source selection and preview identity.
failure / error behaviour: Unknown/foreign preview/source IDs return scoped fixed errors. No upload endpoint or collection native tool.
edge cases: Human corrections append interpretation revisions; exported facts remain immutable.
integration points: Engineer owns action/query validation and scope fencing. Builder owns presentation, integration fixtures and browser verification after contracts stabilize.

TESTS_NEEDED
- Both-platform public exporter tests: actual protected standalone run, terminal export, missing ACTIVE binding, unsupported old pin, immutable origin unchanged.
- Export integrity tests: path-bearing artifact attacks, aliases, altered/missing bytes, secret canaries, bounds, mixed/incomplete pages and changed snapshot.
- Collector integration tests: real exporter→retention→SQLite; every crash boundary; exact/conflicting request/export/source replay; source removal after retention.
- Domain tests: no fabricated Harness identity, foreign repository/run refusal, no upload/path/command fields, no native collection capability.
- Projection tests: schema migration performs no historical scan/copy; batches ≤100; preserved IDs; concurrent repeat/checkpoint conflicts; atomic new R1 insertion.
- Mixed-query tests: projection between pages produces no duplicate/moved incident; scoped filtering; read-only GET; unindexed actions refused.
- Browser tests: source switching, stale responses, conflicts, coverage/progress and desktop/narrow alignment.

EARLY_SUPPORT_SAFE
- none

DEFERRED_SUPPORT
- Dashboard presentation and public integration fixtures wait for exporter/collector contracts.
- Crash, pagination and migration fixtures wait for final transaction and cursor schemas.
- Both-platform packaging/parity verification waits for final exporter registration and closure changes.

OPEN_QUESTIONS
- []
