PSEUDOCODE

## `harness/db.py`, proposed `harness/migration_compatibility.py` (CORE)
purpose: preserve current-main’s no-write-lock opening of fully migrated databases while applying the previously specified compatibility bridge.
signatures: preserve `connect(path: Path) -> sqlite3.Connection`, `apply_migrations(conn: sqlite3.Connection) -> list[str]`, `_migrations()`, `transaction(conn)`; retain previously proposed compatibility helpers.
inputs / outputs: checkpoint `ded9054310d1f8ffe90736b192fcfaec557be3c9`; main `fef2cd1ec5650c7fba5cf9f056302d105f5c64a5`; canonical reliability versions 0021–0025.
state changes: none on a fully canonical current database; recognized reconciliation/missing migrations alone require writes.
control flow:
  1. Discover and validate packaged migration identities.
  2. Read ledger and managed schema in a consistent read snapshot; validate exact canonical filenames and compatible cumulative schema.
  3. If fully current and canonical, return `[]` without `BEGIN IMMEDIATE`, ledger updates, or migration DDL.
  4. If reconciliation or missing migrations are required, end the read snapshot before acquiring the write transaction. Reread and revalidate after acquiring it.
  5. Perform the previously specified atomic checkpoint ledger relocation; then apply genuinely missing migrations transactionally.
  6. Preserve pre-migration `register_session_protocol(conn)` and application `guard_legacy_transaction(conn)`. Keep COMMIT inside rollback protection.
data flow: read-only classification → optional locked revalidation → recognized mutation.
failure / error behaviour: unknown or incompatible histories refuse without repair; concurrent changes invalidate the earlier classification and require locked revalidation.
edge cases: another connection holding a long write transaction must not prevent an already-current WAL reader from opening; “all numeric versions present” alone is insufficient.
integration points: current-main `apply_migrations()` explicitly avoids taking a write lock for already-applied versions; earlier Designer step requiring unconditional `BEGIN IMMEDIATE` must be narrowed accordingly.

## `harness/execution_check_channel.py`, `execution_check_process.py`, `execution_processes.py`, `execution_isolation.py` (CORE)
purpose: compose short private process TMPDIR lifetime with retained handles and durable policy liability.
signatures:
- Preserve `CheckChannel(..., storage=None, policy_attempt=None)`, `observe(handle=None)`, `stop(handle=None)`.
- Preserve `check_profile(context_path, cwd, process_temporary=True)`, `CheckProcess`.
- Preserve `create_process_temporary()`, `release_process_temporary(path)`, `process_temporary_environment(profile, environment)`.
- Preserve `OperationIsolation.process_temporary`, `al_writable`, `al_sockets`.
inputs / outputs: one trusted private directory and one `ProcessHooks` instance per launched handle.
state changes: each handle record owns `process_temporary`, process, observer, policy hooks, completion and storage association.
control flow:
  1. Create the pending handle record before launch; retain original Attempt/context.
  2. Create that handle’s short TMPDIR; store its identity in the record immediately.
  3. Prepare its policy hooks and launch authority; pass only its path through `HARNESS_CHECK_PROCESS_TMP`.
  4. The child adopts the path through `check_profile`; ordinary standalone `check_popen` retains its existing wrapper ownership.
  5. Attach the returned process before storage/observer callbacks can fail.
  6. On proven no-start, release the owned temporary directory and use the exact no-start accounting transition.
  7. On observed leader reaping plus owned-group settlement, release that handle’s TMPDIR according to upstream cleanup semantics.
  8. Retain unresolved policy liabilities independently. TMPDIR removal, storage completion, group disappearance and publication acknowledgement never establish `boundary_closed`.
  9. Timeout/EOF/cancellation inspect every retained handle; cleanup failure for one must not discard another.
data flow: handle → TMPDIR/process/hooks/storage; no channel-global temporary-directory or hook replacement.
failure / error behaviour: after launch becomes possible, failed callbacks or ambiguous cleanup retain unknown liability. Do not infer no-start from an exception after a process was returned.
edge cases: acknowledge old handle after a new launch; surviving descendants; repeated observation; adoption/profile-construction failure; classification revalidation failure.
integration points: `ProcessHooks.prepare/spawned/finish`, `ExecutionStorage.completed`, `CheckProcess._settled`; upstream process temporary helpers live in `execution_isolation.py`, not a separate `process_temporary.py`.

## `harness/execution_operations.py`, `harness/policy/resources.py`, `control_boundary.py`, `control_qualification.py` (CORE)
purpose: prevent new upstream convenience paths from performing unqualified effects during policy preparation.
signatures:
- Preserve `read_only(args)`, `OperationController.profile(self, writable=(), *, al=False)`, `al_cache_grants(self)`.
- Extend `profile` with an explicit internal keyword selecting whether AL optimizations are permitted; the sealed-boundary caller must select the non-effectful preparation path.
- Preserve `resources.prepare(operation, controller, principal, name, a, state)` and `control_boundary.prepare(operation, interpreter, *, al=False)`.
inputs / outputs: parsed AL arguments, original Attempt, exact boundary qualification.
state changes: ordinary admitted paths retain upstream cache/service behavior; sealed preparation creates no cache/service grants.
control flow:
  1. In `resources.prepare`, import and call upstream `read_only(args)`; set `mutating = not read_only(args)`.
  2. Apply the same predicate at authority-mode and worker-dispatch decisions. `next --do` must not inherit bare `next` read authority.
  3. Detect exact retry and six-argument finish before evaluating any AL optimization.
  4. For sealed qualification, construct the AL profile without calling `al_cache_grants`, `Locator`, `_git_locations`, `al_grants` or `_private_directory`.
  5. Ensure the resulting sealed profile has no shared cache writes or service sockets; do not call an effectful helper and then discard its grants.
  6. Ordinary policy-enforced AL paths may use new effects only after their actual resource/launch authority is established. Until a corresponding method is qualified, refuse the unsupported effect or use the existing no-optimization execution path before any effect.
  7. Preserve legacy/unenrolled behavior and current-main semantics where the existing policy permits it.
data flow: command classification → authority/claims → selected execution profile; profile construction cannot create effects ahead of its own admission.
failure / error behaviour: unsupported qualification returns `UNSUPPORTED_ENFORCEMENT_CAPABILITY`; a partially executed optimization cannot be silently converted to “unstarted.”
edge cases: warm Locator versus cold lookup; absent service; cache-directory failure after earlier directory creation; shared Git common directory across worktrees.
integration points: `al_cache_grants()` invokes `Locator.__call__()` and `al_grants()`; these are not pure grant descriptions. `services_root()` launches the managed launcher; `_git_locations()` launches Git; `al_grants()` creates shared directories.

## `al/installer/runtime_run.py`, `runtime_pin_boot.py`, `runtime_pin_cache.py`, `runtime_service_boot.py`, `runtime_service_client.py`, `runtime_retry_control.py`, protected launcher generation (CORE)
purpose: retain upstream runtime optimization without extending sealed closure by source hashes alone.
signatures: preserve `run(..., existing_run=False, private_session=False)`, `verify_pin_cached(..., readonly=False, captured=None, write=True, create=None)`, `ensure(...)`, `activate(...)`, `complete(...)`.
inputs / outputs: exact authenticated sealed request or ordinary runtime invocation.
state changes: no service routing/startup or verification-cache publication in the sealed method unless separately qualified.
control flow:
  1. Audit the generated launcher before its service fast path. A control transport request must reach authenticated sealed activation rather than resident command execution.
  2. Treat presence of a control descriptor only as a reason to bypass optimization; it grants no authority. Invalid transport still refuses at activation.
  3. Preserve ordinary service dispatch/startup unchanged.
  4. In authenticated sealed execution, skip `ensure_service`; do not rely solely on mutable environment configuration.
  5. Keep pin verification authoritative. Use full verification or the existing cache API with `write=False`; `create=False` alone is insufficient because it can still publish into an existing directory.
  6. Preserve `PIN_VERIFY` captured-byte verification and the child’s `verified["bytes"] == captured` check.
  7. Source-audit the entire newly reachable retry/finish callgraph. Preserve brokered exact Git forms, exec permits, no-fork probes and completion binding.
  8. Regenerate candidate closure records only after behavior is settled. Newly present files or matching hashes do not qualify their effects.
  9. If any reachable helper/effect remains outside the exact method, refuse before execution; do not emit a closed receipt.
data flow: immutable launcher/pin → authenticated exact method → bound helper requests → invocation-specific completion.
failure / error behaviour: cache corruption is a full-verification fallback; unsupported service/helper behavior is a qualification refusal.
edge cases: warm resident service, warm/cold verification cache, historical pins lacking cache/service modules, attempted alternate argv, copied or stale descriptor.
integration points: `runtime_service_boot.ensure` may create routes/logs/locks and spawn a long-lived process; `runtime_pin_cache._store` performs atomic writes. Neither is covered merely by a successful short-lived launcher exit.

## `harness/execution_al_service.py`, `execution_isolation.py`, `policy/processes.py`; both AL `al_writers.py` / `al_intent.py` trees (CORE)
purpose: keep remote service effects and automatic evidence execution outside the six-argument finish closure.
signatures: preserve `SnapshotRelay.answer`, `operation_popen`, `ProcessHooks.finish`, `cmd_writer`, `writer_finish_auto(a, sid)`.
inputs / outputs: service snapshot requests; exact finish arguments; evidence commands.
state changes: no inferred closure of a resident service or evidence process from the requesting launcher’s completion.
control flow:
  1. Preserve Linux relay’s snapshot-only framing, descriptor transfer and refusal of other operations.
  2. Do not supply service sockets/relay descriptors to the sealed profile: even a snapshot request can cause work in a resident process outside the sealed child.
  3. For ordinary permitted relay use, retain its existing restricted interface; relay EOF is not whole-tree settlement.
  4. Preserve upstream `writer_finish_auto`: it first dispatches ordinary finish, then selects and runs relevant evidence areas.
  5. Policy qualification continues accepting only the exact six-argument finish. Reject `--evidence auto` before any finish mutation under that sealed method.
  6. Do not normalize away extra arguments, reuse the finish receipt for evidence, or set automatic evidence execution to nonmutating.
  7. Supported evidence remains available through its normally admitted commands/check handles. Its liabilities remain separate from finish.
data flow: finish receipt → exact finish only; evidence handles → their own admission/storage/process accounting.
failure / error behaviour: automatic evidence failure may leave the writer finished in upstream ordinary execution; report that actual state, never an atomic rollback.
edge cases: unsuccessful outcome with `--evidence auto` still uses a different argv and must not opportunistically inherit the sealed signature.
integration points: existing `ProcessHooks.finish` recognizes only exact qualified boundaries; a successful service response, group disappearance or cache hit cannot substitute for `control.qualifies(process)`.

TESTS_NEEDED
- `tests/test_db.py`, `tests/test_state_db_busy.py`: fully canonical connect succeeds while another connection holds a write transaction; no migration write lock; recognized reconciliation still rereads under lock. PUB-02.
- `tests/test_guarded_check_channel.py`, process-policy tests: distinct retained-handle TMPDIRs/hooks; no-start cleanup; post-launch callback failure; old-handle acknowledgement; group cleanup never releases unresolved policy claims. PUB-03.
- `tests/test_policy_dispatch.py`: bare `next` versus `next --do`; worker authority; automatic finish rejected before mutation under sealed qualification. PUB-04/PUB-06.
- `tests/test_policy_retry_control.py`: exact retry/finish succeeds only with qualified closure; spies prove qualification never invokes cache/service preparation; extra finish flags refuse; prior liabilities survive. PUB-06.
- `tests/test_al_sandbox_grants.py`, `tests/test_al_service_relay.py`: ordinary grants/relay preserved; sealed profile contains neither; no relay-to-command escalation. PUB-03/PUB-06.
- `al/tests/test_runtime_pin_cache.py`, `test_runtime_service.py`, `test_runtime_service_unit.py`: ordinary warm/cold behavior retained; sealed launcher never routes to service, starts service or publishes cache records; captured-byte checks remain mandatory. PUB-06.
- Both AL distributions’ writer/intent tests: ordinary automatic evidence behavior retained; exact sealed finish does not run evidence or borrow its proof. PUB-05/PUB-06.
- Actual merged-candidate closure/parity and required repository checks remain outstanding; this inspection ran no tests, services or database connections. PUB-08.

EARLY_SUPPORT_SAFE
- none

DEFERRED_SUPPORT
- Builder regression tests wait for Engineer’s final internal profile-selection interface and launcher guards.
- Candidate closure records wait for all reachable source changes and method qualification.
- Documentation and remaining-feature handoff wait for actual supported/refused paths and fresh verification results.
- Full caller/test-body coverage and generated-launcher review remain final verification obligations; this narrow delta inspected relevant implementations and selected caller/test locations, not every upstream service implementation or test body.

OPEN_QUESTIONS
- []