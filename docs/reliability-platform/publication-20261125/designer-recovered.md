PSEUDOCODE

## `harness/db.py`, new `harness/migration_compatibility.py`, `harness/migrations/` (CORE)
purpose: reconcile the two known migration histories without rewriting published main migrations or recreating checkpoint data.

signatures:
- Keep `connect(path)`, `transaction(conn)`, `_migrations()`, `apply_migrations(conn)`.
- Add `reconcile_checkpoint_history(conn, migrations) -> None`.
- Add `validate_history(conn, migrations) -> History`.
- Add `execute_migration(conn, sql) -> None`.

inputs / outputs:
- Main identity: `6b1da293329c58c2dec1e548c599694abc9a80cf`.
- Checkpoint contribution includes its uncommitted production files; HEAD alone is not that contribution.
- Preserve main `0019_investigation_sources.sql` and `0020_planning_sessions.sql` byte-for-byte.
- Canonical checkpoint mapping:

| Checkpoint identity | Canonical identity |
|---|---|
| `0019_reliability_observations.sql` | `0021_reliability_observations.sql` |
| `0020_reliability_timeline.sql` | `0022_reliability_timeline.sql` |
| `0021_runtime_policy.sql` | `0023_runtime_policy.sql` |
| `0022_dependency_launch_fencing.sql` | `0024_dependency_launch_fencing.sql` |
| `0023_incident_catalog.sql` | `0025_incident_catalog.sql` |

state changes:
- Reconciliation changes only recognized `schema_migrations.version/name`; preserve `applied_at`.
- Ordinary missing migrations subsequently create their real schemas.
- No observation, incident, policy, receipt, approval, session, or investigation backfill.

control flow:
1. Load and validate packaged migration filenames before migration mutation; retain duplicate-version refusal.
2. `connect()` registers `planning_session_store.register_session_protocol(conn)` before applying migrations. Preserve WAL, foreign keys, busy timeout, permissions, and connection cleanup.
3. Maintain a checked-in, bounded schema-signature manifest for the recognized histories. Derive it from the exact captured SQL during implementation; test it against disposable databases built from those scripts.
   - Describe columns, generated columns, defaults, primary/unique keys, foreign keys, indexes and trigger definitions.
   - Validate cumulative schema, including runtime-policy’s replacement of `reliability_timeline_links` and launch-fencing’s three `execution_attempts` columns.
   - Include main investigation/session objects independently.
   - Normalize insignificant SQL formatting only; do not discard constraints, literals, trigger bodies or quoted identifiers.
   - Ignore SQLite-owned implementation objects; unexpected conflicting objects in the managed namespaces refuse.
4. In `BEGIN IMMEDIATE`, reread the complete ledger and schema:
   - Accept fresh/common-prefix histories through 0018.
   - Accept main’s canonical prefix.
   - Accept the exact checkpoint prefix through any of 0019–0023.
   - Accept the canonical reliability prefix plus zero, one or both main migrations. This is the explicit crash-resume state after reconciliation.
   - Require exact filenames, contiguous recognized branch prefixes and the corresponding cumulative schema. A numeric match alone never establishes identity.
   - Unknown names, holes outside the documented transition, destination collisions, absent expected schema, partial schema or incompatible mixed identities refuse before changing the ledger.
5. For checkpoint history, update rows in descending old-version order. Each update uses the exact old version/name predicate, checks one affected row, and writes its canonical version/name. Commit the entire relocation together.
6. Apply missing canonical migrations in order, each in its own `BEGIN IMMEDIATE` transaction:
   - Reread and validate the current history after acquiring the lock.
   - Existing row must have the canonical filename and compatible cumulative schema.
   - For a missing row, execute the migration, validate the resulting schema, then insert its ledger row.
   - Commit schema and ledger together.
7. Replace reliance on `executescript()` inside an already-open transaction with an explicitly transactional executor:
   - Accumulate SQL through candidate semicolons; use `sqlite3.complete_statement()` to recognize complete statements, including trigger bodies.
   - Execute one complete statement with `conn.execute()`.
   - Reject malformed trailing input and migration-level transaction-control statements.
   - Do not split blindly on semicolons or concatenate unescaped ledger values into SQL.
8. Preserve checkpoint rollback handling, with COMMIT inside the protected `try`. Preserve main’s `guard_legacy_transaction(conn)` immediately after BEGIN for application `transaction()` calls. The schema runner uses its narrowly scoped internal transaction path; it does not grant application writes a session-guard exemption.

failure / error behaviour:
- Failed reconciliation leaves the original ledger unchanged.
- Crash after reconciliation but before main0019/0020 leaves a recognized resumable canonical history.
- Failed SQL rolls back both that migration’s DDL/DML and ledger row.
- Refusal identifies versions/object names and the expected history, without dumping database contents.
- No automatic “repair” by marking a missing schema applied, dropping objects or clearing rows.

edge cases:
- A copied schema without its trustworthy ledger is not a recognized migration history.
- Concurrent upgraded connections serialize on SQLite and reread after locking.
- This does not make concurrent old checkpoint binaries safe. Publication closes the old writer before upgrading; historical pinned runtimes are not edited.
- The runtime-policy timeline table rebuild must preserve actual timeline rows and links; its migration fixture must contain populated data.

integration points:
- `planning_session_store.register_session_protocol`.
- `planning_session_events.guard_legacy_transaction`.
- Main 0020 attached-session triggers continue invoking the registered SQL functions.
- Existing incident catalog constraints remain unchanged, including the absence of a fabricated R7 evaluation relation.

## `harness/execution_check_channel.py`, `execution_check_process.py`, `policy/processes.py`, `execution_processes.py`, `storage.py` (CORE)
purpose: compose retained check handles and storage publication with exact policy process accounting.

signatures:
- Merge constructor as `CheckChannel(context_path, validate, *, storage=None, policy_attempt=None)`.
- Preserve main’s `stop(handle=None)`, `observe(handle=None)`, retained `records`, selected handle protocol and publication acknowledgement.
- Preserve `ProcessHooks.prepare(handle, observer)`, `spawned(process)`, `finish(closed=..., outcome=...)`.
- Preserve `ExecutionStorage.begin`, `started`, `completed`, `retain`, `acknowledge`.

state changes:
- Each retained handle owns its own process, classification, deadline, observer, `ProcessHooks`, completion and storage linkage.
- Never move a previous handle’s hooks or completion into the new active handle.
- Policy rows remain authoritative independently of the in-memory handle map and observational spans.

control flow:
1. Capture the original operation’s `ProcessContext` and owning `Attempt` explicitly when creating the channel; the channel thread must not obtain a different/default context.
2. On `start`:
   - Validate current authority, framing, descriptors, argv/cwd and classification using main’s checks.
   - Enforce main’s retained-handle capacity and running-handle rule.
   - Validate the managed-storage reservation and retain main’s recipe/materialization binding.
   - Create the in-memory pending handle record before attempting Popen.
   - Create the exact durable observation/process linkage and call its `ProcessHooks.prepare`.
   - Reserve/authorize launch using the existing Attempt; derive the handle deadline from the original policy deadline and main’s 900-second ceiling. Starting another handle cannot reset a consumed policy deadline.
   - Launch only the existing isolated check entrypoint, then attach the process immediately to that handle; call `spawned()` and storage `started()`.
3. A failure before any launch permit/effect uses only the existing exact fenced no-start transition. A failure after launch became possible retains unknown liability unless exact evidence proves otherwise.
4. `observe(handle)` records immutable completion for that handle:
   - Preserve main’s `leader_reaped`, `owned_group_settled`, `descendant_scope="group"`, `descendants_settled=None` and settlement basis.
   - Feed the same completion to storage.
   - Notify that handle’s observation and policy hooks separately.
   - Group absence and parent wait do not set policy `boundary_closed`.
5. On completion/publication acknowledgement:
   - Revalidate the requested handle, current reservation, allocation, declaration, artifacts, output bounds, source/classification binding and recipe reconciliation.
   - Preserve main’s conflicting-publication/completion refusal and idempotent acknowledgement.
   - Do not call policy settlement merely because publication was acknowledged, evidence passed, or storage accounting completed.
6. On cancellation, timeout, EOF or channel close:
   - Act on exact owned handles, including retained records needing final observation.
   - Preserve missing-ack retention.
   - Do not report channel settlement merely because the channel thread exited.
   - A timeout or exception while cleaning one handle cannot silently discard another handle’s durable obligations.
7. Preserve `Attempt.owner_finished()`/`finish_processes()` distinction:
   - Owner return does not imply every subordinate process closed.
   - Existing exact process-instance and current-supervisor/epoch settlement checks remain.
   - All unresolved `boundary_closed=0` liabilities retain their resource claims, even if another process returned successfully.
   - Qualified sealed retry/finish receipts apply only to their exact invocation and helper closure; they do not qualify arbitrary checks.

data flow:
- `handle → storage allocation/reservation`.
- `handle → policy_processes instance → original Attempt/resource claims`.
- `handle → durable process span`.
- Publication reference is evidence, not a replacement for any of these identities.

failure / error behaviour:
- After Popen, storage/observer failure must still leave the process owned and conservatively accounted.
- A supervisor restart cannot reconstruct successful closure from a PID, group probe, missing handle or publication.
- Preserve safe checkpoint diagnostics; do not expose main’s raw exception strings where they could contain commands, paths or secrets.

integration points:
- Preserve main `execution_check_process.check_profile`, managed-storage validation, `recipe_argv`, protected recipe materialization and classification wrapper.
- Preserve checkpoint isolation/deadline refusals. Main’s additional storage capability is not a new whole-tree containment qualification.
- `ExecutionStorage.completed()` already retains allocations when `producer_settled(completion)` is false; keep that behavior.

## `harness/execution_operations.py`, `task_environment.py`, `task_tooling.py`, worker/bootstrap and policy consumers (CORE)
purpose: retain prepared-check planning without bypassing owning setup admission or strict source consumption.

signatures:
- Preserve `task_unit(..., *, verify_outputs=True)`.
- Preserve checkpoint `prepare()` requirement for the exact owning `environment_operation` Attempt.
- Add main’s `plan_defaults`/`SetupReport.on_ready` behavior only through the admitted continuation.
- Preserve `auto_prepare()` queue semantics and the existing setup continuation.

control flow:
1. `auto_prepare()` selects bounded concrete units and queues requests with current task/revision bindings. It does not invoke `prepare()` directly under the surrounding AL Attempt.
2. Setup continuation creates its own admitted environment operation, establishes dependency/resource claims before source reads, then calls `prepare()`.
3. Preserve main completion-time task validation alongside checkpoint exact principal/task/revision/epoch ownership:
   - Revalidate before declaring tooling ready or planning checks.
   - Validate lock inputs while the original source-consumption claim remains effective.
   - Completion callbacks use explicitly retained ownership/context, not whichever operation happens to be current on the worker thread.
4. Main prepared-check planning may publish its descriptor and invoke the existing AL evidence-plan path only through the corresponding normal admission.
   - No direct nested transaction.
   - No controller/artifact-lock inversion.
   - If planning is a distinct effect after setup completion, queue it as a separately admitted continuation rather than borrowing an unrelated or finished Attempt.
   - Return truthful Ready/Pending/Unavailable distinctions; setup readiness is not test evidence.
5. Retain checkpoint `require_dependency_outputs` strict marker, same-Attempt claim lifetime and reciprocal native launch-presence fencing.
6. Preserve main lifecycle/storage additions in worker and MCP wiring, without weakening exact epoch, native launch intent, current authority or diagnostic fences.
7. Typed immutable publication continues using exact validated buffers and metadata-only dependency checks where specified. It cannot erase prior native/legacy liabilities or become proof of coordinated mutable-source consumption.
8. Incident capture remains attached to the actual source transition/receipt. Neither storage acknowledgement nor a planning rebind fabricates an incident source or verified completion.

failure / error behaviour:
- Admission failure remains failure; no fallback direct setup or AL execution.
- Revocation may prevent new effects while cleanup remains available.
- A successful later setup/check cannot release earlier unresolved claims.

## Both AL script trees and `al/installer/` integration (CORE)
purpose: preserve both validation chains and rebuild only the new candidate’s qualified closure.

control flow:
1. Combine main planning-rebind/readiness/retained-phase handling with checkpoint typed companions, reconciliation observations and incident receipts at their actual command handlers.
2. Existing guards run before save/publication. A typed companion is not a rebind receipt; a rebind receipt is not typed-output or incident validation.
3. Preserve `manifest.required(..., release=...)` and `release_compatibility.required_scripts(layout, release)`:
   - Historical release minima remain the explicit source-audited set.
   - Do not infer compatibility from missing files.
   - New candidate requirements include the merged resources.
4. Preserve main descriptor-based `source_materialization` custody, exclusive destination creation, content/mode verification and owned cleanup. A materialized copy grants neither execution nor cleanup authority.
5. Recompute the candidate’s protected import/resource closure after both script trees and installer source settle.
6. Requalify exact sealed retry and writer-finish methods against that closure. If newly reachable code introduces an unqualified helper or effect, the method refuses until the candidate qualification covers it; no generic AL allowance.
7. Do not rewrite old manifests, release pins, runtime authority or historical completion receipts.

## Dashboard, packaging and remaining-feature handoff (SUPPORT)
purpose: integrate presentation and distribution after the core contracts settle.

control flow:
- Preserve main planning-session/source-investigation routes alongside checkpoint incident/timeline/policy routes; they are separate domain operations.
- Preserve operator, Host/Origin, body-size and selected-source checks.
- Keep main `harness` plus `al.installer` packaging and include all canonical migration/resource files.
- Document recognized migration histories and refusal recovery without suggesting ledger edits.
- Mark R5b–R11 as remaining work requiring merged-consumer revalidation. No placeholder schema or producer is introduced to claim them complete.

TESTS_NEEDED
- `tests/test_db.py`: fresh/common prefixes; main through 0019 and 0020; every checkpoint prefix; populated timeline/policy/catalog data; exact remapping and preserved timestamps; repeat/concurrent connections; crash before/during/after remap and migration; unknown filenames, partial schemas, destination collisions and absent-schema refusal; trigger-aware SQL execution and rollback.
- Main planning-session tests: SQL functions registered before migration; attached-session guards and publication triggers still reject unauthorized writes; main investigation data survives checkpoint upgrade.
- Retained-check/storage tests: multiple handles, old-handle acknowledgement after a later launch, conflicting acknowledgement, missing acknowledgement, per-handle deadline, capacity, EOF and cancellation.
- Combined policy/check tests: Popen followed by storage failure; exact no-start; unknown crash; passed handle plus unresolved sibling; group-only completion never sets boundary closure or releases sibling claims; current supervisor versus revoked original principal.
- Setup/tooling tests: queued auto-prepare obtains its own Attempt; no direct fallback; current task validation at completion; source drift and policy denial; prepared-check planning has its own required admission; no lock inversion.
- Both AL trees: planning rebind/recovery plus typed-output/incident publication; protected membership/parity; sealed retry/finish helper closure; historical release compatibility and materialization custody.
- Merged candidate: required lint, format, nonbrowser tests, build/resource inventory, browser suite and desktop/narrow visual review. Checkpoint E52–E57 results remain checkpoint evidence, not merged-candidate evidence.

EARLY_SUPPORT_SAFE
- none

DEFERRED_SUPPORT
- Dashboard, packaging, documentation and merged-candidate tests wait for Engineer’s migration and process/publication interfaces.
- Regenerated closure records wait for all candidate core source changes.
- Remaining-feature handoff updates wait for the actual merged consumer signatures.

OPEN_QUESTIONS
- None requiring a user decision. This protocol is grounded in the inspected checkpoint and main commit; the final captured checkpoint and integration candidate must be revalidated before implementation evidence or publication claims.
