PSEUDOCODE

## harness/policy/contracts.py, composition.py, resources.py (CORE)
purpose: Add closed policy contracts and derive authority exclusively from existing trusted boundaries.
signatures: Add `derive_intent(operation, controller, principal, name, arguments, state) -> OperationIntent`, `compose(policies, inherited) -> EffectivePolicy`; retain `OperationController.authority(...)`, `admitted(...)`, `PrincipalRegistry.enroll(...)`, `classify_command(request, context=None)`.
inputs / outputs: Authenticated principal, current execution envelope, admitted assignment, handler-specific arguments → immutable intent and effective policy.
state changes: None during derivation.
control flow:
  1. Validate exact fields, schema versions, bounded collections, integer amounts, recognized units and enumerated rules. Reject executable expressions, cycles, missing parents and unknown versions.
  2. Call existing authority, assignment, dependency, writer, revision and classification guards. Policy permission never replaces these calls.
  3. Resolve applicable global/project/workflow/role/execution versions and every inherited restriction. Sort immutable references before canonical digesting.
  4. Explicit deny wins. Intersect allow constraints; union required approvals and pool references. Deduplicate the same pool, never charge it twice because multiple ancestors reference it.
  5. Derive principal ancestry from registry records; execution/epoch/generation/spec from current trusted envelope; task revision and writable scope from `admitted`.
  6. Derive patch resources from `parse_patch` plus canonical confined paths. Derive command effects from closed command grammar and actual admitted isolation profile. Unknown commands are write-capable, irrespective of caller `effects` or `target`.
  7. Represent command filesystem authority as its entire allowed scope, not guessed actual writes. A policy restricting it further must narrow its OS profile or deny.
  8. Resolve network/database/production identity only from configured trusted profiles. Unresolved identity or unenforceable required restriction denies.
data flow: Persist opaque resource references; protected immutable resources hold sensitive exact approvals. Do not persist commands, secrets, or hashes derived from sensitive command payloads.
failure / error behaviour: Closed codes including `POLICY_INVALID`, `RESOURCE_UNRESOLVED`, `AUTHORITY_STALE`, `POLICY_DENIED`, `APPROVAL_REQUIRED`, `UNSUPPORTED_ACCOUNTING_DOMAIN`, `UNSUPPORTED_ENFORCEMENT_CAPABILITY`.
edge cases:
  - No optional policies preserves existing authority behavior.
  - No required authorization denies.
  - Outbox writes, environment preparation, delivery and AL commands need explicit handler classification; a generic “read” label must not authorize their effects.
integration points: `OperationController.dispatch/_dispatch/al_command`; existing protected file descriptors and OS isolation remain authoritative.

## harness/migrations/<next_available>_policy.sql, harness/policy/store.py (CORE)
purpose: One transactional accounting authority; no cross-database atomicity claims.
signatures: Add closed tables for approved contracts, pool membership, operation attempts, capability uses and bounded decision audit; use existing `harness.db.transaction(conn)`.
inputs / outputs: Validated records → durable decisions, reservations and attempt references.
state changes:
  - Store all applicable pools, capability uses and decisions in the execution’s existing project `state.db`.
  - `execution_tree` owner is a stable root execution reference.
  - `workflow_instance` owner is an explicitly registered stable instance reference.
  - `named` shared pools may span executions/workflows only inside that same database.
  - Reject references requiring another database or an implicit machine-wide/global counter.
control flow:
  1. Add foreign keys and uniqueness for `(intent_id,pool_id)`, `(capability_id,intent_id)` and stable attempt identity.
  2. Require nonnegative bounded integers and recognized states. Check arithmetic before addition; reject overflow.
  3. Keep pool identity independent of policy version, child identity, retry, native invocation and epoch.
  4. Store immutable policy versions separately from mutable active bindings.
  5. Replacement updates the existing pool’s approved limit/version binding; never zeroes counters. If its limit falls below existing commitments, deny new work until capacity exists; preserve existing charges.
  6. Persist admission audit in the same transaction. Export/project to observability afterward using decision ID for deduplication.
data flow: Public views expose consumed/reserved/measured/unknown distinctly. `observed_units=NULL` never means zero.
failure / error behaviour: Database failure prevents admission. Migration numbering must be selected after preceding increments land.
edge cases: Retried requests return their recorded decision but never obtain a second executable start permit.
integration points: Existing `connect`, `transaction`, execution foreign keys and dashboard database access. No new service.

## harness/policy/admission.py (CORE)
purpose: Linearize overlapping pool reservations, capability consumption and authorization.
signatures: Add `admit(...) -> PolicyDecision`, `start_attempt(...) -> StartPermit`, `release_unstarted(...)`, `settle(...)`.
inputs / outputs: Server-derived intent and current authority → durable reservation and single-use in-memory execution permit.
state changes: `reserved → started → settled|unresolved`; `reserved → released_proven_not_started`.
control flow:
  1. Lock order for operation admission: existing `OperationController.lock`, shared `epoch_action_lock`, then SQLite `BEGIN IMMEDIATE`. Never acquire the controller/process lock while holding a ledger transaction.
  2. Inside the transaction, explicitly recheck expected epoch/generation, current approved policy binding, principal release/revocation state, current specification and assignment identity.
  3. For an existing intent: return its recorded state without authorizing execution again.
  4. Validate every required capability and every pool before changing any:
       `available = limit - consumed - reserved`
       require `units <= available`.
     One write-capable invocation requests one mutation unit. An admitted retry requests one retry unit tied to the identified failed attempt.
  5. Insert durable attempt in `reserved`, all reservations, capability-use records and allow decision atomically:
       `UPDATE policy_pools
        SET reserved=reserved+:units, revision=revision+1
        WHERE pool_id=:id AND state='active'
          AND :units <= limit-consumed-reserved`
     Require one updated row per pool; otherwise roll back everything.
  6. Capability use counters increase in that same transaction. Default capability maximum uses is one. A failed/nonstarted operation does not restore a capability use.
  7. Commit is the admission linearization point.
  8. Immediately before irreversible work, reacquire shared epoch claim and transaction; revalidate current authority, policy digest, capability revocation/expiry and attempt state.
  9. CAS attempt `reserved → started`; set trusted start/deadline. Move discrete mutation/retry amounts from `reserved` to `consumed`. Commit.
  10. This commit is the conservative irreversible-start boundary. Return a nonserializable permit only to the caller that won the CAS; release SQLite/epoch locks before executing external work.
data flow: A committed start may precede actual execution. This intentionally charges a crash window rather than risking duplicate effects.
failure / error behaviour:
  - Denial commits only a bounded closed audit record, without raw request/resource text.
  - A denied start can release still-reserved capacity only by the guarded release transition.
  - Once started, failure, cancellation or “no files changed” never refunds mutation/retry units.
edge cases:
  - Revocation committed before admission denies admission.
  - Revocation committed between admission and start prevents start.
  - Revocation after start does not reverse the already-started operation; best-effort owned cancellation is separate.
  - Policy replacement invalidates not-yet-started reservations’ old digest. Started attempts retain their original accounting contract.
integration points: All effectful supported handlers enter this gate after existing authorization and before first effect.

## harness/policy/capabilities.py, harness/execution_decisions.py (CORE)
purpose: Mint narrowly bound grants from existing explicit developer decisions.
signatures: Add `issue_capability(controller, decision_ref, approved_resource_ref, binding)`, `revoke_capability(controller, capability_id, expected_generation)`; retain `record_execution_decision`, `materialize_decision`, `deliver_decision`.
inputs / outputs: Exact approved developer decision and immutable protected authorization resource → capability ID.
state changes: Immutable grant binding plus mutable use count, state and revocation generation.
control flow:
  1. Use the existing decision/Registry workflow; never interpret arbitrary positive prose as an executable permission.
  2. Require an explicit structured authorization resource approved by that decision. Validate its Registry provenance, materialized delivery, current acknowledgment and exact execution/spec binding.
  3. Bind operation kind, canonical resource set, principal or role, epoch/generation, policy digest, use/unit maxima and expiration.
  4. Mint idempotently under shared epoch claim and ledger transaction.
  5. During admission, compare every binding exactly and enforce both maximum uses and cumulative maximum authorized units.
  6. Revoke under the same ledger transaction discipline; increment generation and record closed audit.
data flow: Native callers may reference capability IDs; they cannot supply preauthorized intents or mint grants.
failure / error behaviour: Missing/mutated provenance, expiry, wrong binding or spent grant denies.
edge cases: Expiry uses trusted supervisor time; detected rollback/clock anomaly invalidates time-dependent grants until trusted time is restored. Revocation cannot restore use counters.
integration points: Existing approval, Registry immutability and decision delivery validation remain unchanged in authority.

## harness/execution_processes.py, execution_files.py, execution_operations.py (CORE)
purpose: Place gates at actual effect boundaries and retain durable attempt identity through launch and collection.
signatures:
  - Extend `ProcessManager.start(owner, profile, argv, cwd, *, task, revision, timeout=30, environment=None, max_timeout=300, pass_fds=(), completion=None, report=None)` with internal attempt/deadline/start/settlement hooks.
  - Extend `apply_patch(root, patch, authorize_paths)` with an internal before-mutation hook.
inputs / outputs: Existing authorized operations plus server-owned attempt context → existing public results linked to durable attempts.
state changes: Preallocate handle and attempt reference before `operation_popen`; persist process linkage after successful launch.
control flow:
  1. Prepare and validate operation parameters, isolation, process capacity and patch contexts before start where possible.
  2. Patch start hook runs before the first `parent_descriptor(..., create=True)`, since directory creation itself mutates. Retain every existing descriptor/path/version check.
  3. Process start hook runs before `operation_popen`. Handle generation moves before launch.
  4. Synchronous `al_command` launch must use the same protocol; background launch is not the only governed path.
  5. Pass an absolute monotonic deadline into `_drain`; do not start a fresh timeout when the drain thread begins.
  6. Completion persists outcome with exact attempt/epoch/generation and authenticated supervisor provenance. Do not perform ledger callbacks while holding `ProcessManager.lock`.
  7. A post-start launch error retains discrete charges. If supervisor cannot prove the launch outcome, mark unresolved.
  8. Poll/cancel/result collection follow a separate closed cleanup classification. Require authenticated ownership and existing disclosure/classification guards, but do not consume mutation/retry budget or require remaining time.
data flow: Process records carry durable attempt ID. Ledger stores opaque process/attempt references, never executable argv.
failure / error behaviour: Collection failure does not imply process failure; signal delivery does not establish closure; missing process handle/PID does not authorize refund.
edge cases: Multifile patches can partially apply; count one invocation and preserve partial/unknown outcome. Read-only subprocesses still require tool/resource policy and deadlines.
integration points: `run_command`, synchronous/background `al_command`, environment preparation, configured delivery/diagram handlers and AL managed-check launch paths require complete boundary coverage before enforcement is claimed.

## harness/policy/reconciliation.py, harness/execution_runtime_recovery.py, execution_settlement.py (CORE)
purpose: Prevent crash, retirement and restart from creating refundable or reusable capacity.
signatures: Add `reconcile_policy_attempts(controller, execution_id, *, expected_epoch_id, expected_authority_generation)`; extend `_settled(controller, execution)` and existing closure-receipt production.
inputs / outputs: Durable attempts plus trusted owned-process/context closure receipts → idempotent settlement.
state changes: Exact reservation/attempt transitions only; never overwrite successor state from an old worker.
control flow:
  1. Settlement obtains a shared epoch claim and transaction, validates explicit epoch/generation, then CASes the exact attempt state.
  2. A `reserved` attempt has no execution permit. Release requires atomically canceling its ability to start plus fencing the owning supervisor/launcher. A racing start and release cannot both win.
  3. A `started` attempt is never replayed automatically, even if no PID was recorded.
  4. Started discrete units remain consumed. Unknown variable usage retains its full conservative bound.
  5. Qualified actual usage can settle a bounded reservation only when the trusted provider settlement proves final liability and the bound was enforced.
  6. Extend runtime `_settled` to account for policy attempts. Before retiring an epoch, prove owned execution closure or refuse activation. Closing a process does not establish token/cost measurement.
  7. Persist unresolved accounting before retirement; successor admissions inherit these commitments. Old callbacks cannot write historical or successor accounting.
  8. A successor may perform current-authority reconciliation against immutable predecessor evidence; that is a new authenticated action, not a historical-writer exception.
data flow: Extend existing `record_closed_context` evidence with exact policy attempt references where appropriate. Its existing parent-exit checks alone must not be promoted into proof of no external effect or no charge.
failure / error behaviour: No heartbeat/PID disappearance inference; uncertain launcher/process closure prevents unsafe activation/refund.
edge cases: Crash before admission commit leaves nothing; after admission leaves reservations; after start leaves conservative consumption; after effect before settlement leaves unresolved outcome.
integration points: Existing worker/epoch recovery locks must retain their order. Never hold SQLite or epoch claims while waiting for subprocess termination.

## harness/policy/deadlines.py, delegation.py; AL retry integration (CORE)
purpose: Preserve inherited budgets and elapsed allowances across children, retry and restart.
signatures: Add internal `inherited_policy_refs(...)`, `remaining_execution_ms(...)`; integrate existing `PrincipalRegistry.enroll`, `OperationController.al_command`, and `cmd_retry(a,sid)` in both shipped AL distributions.
inputs / outputs: Trusted ancestry/workflow identity and persisted failed-attempt identity → inherited constraints and bounded admission.
state changes: Stable execution-tree timer; no per-child reset.
control flow:
  1. Child effective policy includes all parent/root restrictions and pools; child configuration can only narrow.
  2. Replacements and fresh invocations inherit the same accounting owners.
  3. Keep AL retry kind/reason/checkpoint/reconciliation/once-per-role guards. Ledger admission adds a restriction; it cannot authorize an AL-refused retry.
  4. Tie retry ledger intent to its failed-attempt/checkpoint identity. Crash after policy start but before AL state save remains consumed; do not replay without explicit reconciliation.
  5. Execution elapsed allowance is one shared wall interval, not summed child durations. Persist origin and deadline; use monotonic time while supervisor survives.
  6. Restart includes downtime against the fixed deadline. Require trustworthy clock continuity; on unresolvable rollback/reboot uncertainty, deny new time-budgeted work while preserving cleanup.
  7. Operation deadline is minimum of caller request, policy cap and every remaining applicable execution allowance. Subsecond remainder requires internal precision; never round upward.
  8. Explicit token/cost enforcement requires provider-qualified conservative bounds and enforced provider limits. Unsupported configurations fail before provider execution; uncapped usage remains unavailable.
data flow: Native child enrollment can restrict Harness tool authority. It is not evidence that native child generation was prevented.
failure / error behaviour: Codex native spawn has no inspected pre-spawn policy hook. A configured requirement to prevent native delegation or bound its provider usage must reject unsupported enforcement before native execution; do not silently downgrade to enrollment-only control.
edge cases: Deadline exhaustion does not block owned cancellation, bounded result collection or essential reconciliation.
integration points: Existing native authentication, tool restrictions, provider observer refusals and AL retry caps remain intact.

TESTS_NEEDED
- `tests/test_policy_admission.py`: concurrent last-unit admissions, overlapping pools, all-or-nothing capability/pool commit, integer bounds and duplicate intents.
- `tests/test_policy_capabilities.py`: exact binding, one-use races, expiry/revocation ordering, approved-resource provenance and no refund of capability use.
- `tests/test_policy_recovery.py`: every crash window, start/release race, retired settlement rejection, no PID-based refund, successor inheritance and clock uncertainty.
- `tests/test_execution_operations.py`: forged effects/role/resource/epoch refusal, synchronous/background coverage, cleanup after exhaustion and uncapped compatibility.
- `tests/test_execution_files.py`: hook precedes directory creation; confinement and partial-patch accounting.
- `tests/test_epoch_action_races.py`: activation versus admission/start/settlement barriers.
- `tests/test_native_tool_boundary.py`: unsupported native-spawn/provider enforcement refuses before launch; existing bypass refusals remain.
- AL retry tests in both distributions: existing retry guards plus durable failed-attempt budget binding.
- Dashboard/browser tests: actual persisted measured/reserved/consumed/unknown distinctions and visual alignment.

EARLY_SUPPORT_SAFE
- none

DEFERRED_SUPPORT
- Existing dashboard modules: wait for finalized ledger query contracts and reason codes.
- Policy configuration/documentation: wait for exact supported resource selectors, accounting domains and enforcement capabilities.
- Browser verification: wait for the complete admission→settlement→dashboard slice.
- Migration integration: select the next migration number after preceding increments finalize.

OPEN_QUESTIONS
- []
