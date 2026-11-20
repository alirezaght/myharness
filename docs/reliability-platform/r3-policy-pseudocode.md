# R3 policy and budget protocol — AL-Designer output capture

Source: actual `/root/policy_protocol_design` AL-Designer report and immutable-runtime compatibility addendum. Host-side capture because Designer is read-only. This is implementation guidance, not implementation or evidence of passing checks. Read the full original mission and actual Architect brief `/private/tmp/al-r3-policy-plan.md` alongside this artifact.

## Contracts, composition, resource derivation

Validate closed contract versions, bounded nonnegative integers, enums, parent references and acyclic ancestry. Existing execution authority, assignment, dependencies, writer lifetime, revision and classification remain prerequisites. Derive applicable global/project/workflow/role/execution policies and inherited restrictions from trusted registry state. Explicit denies win; allowed tools/resources intersect; approvals accumulate; pools deduplicate. Child restrictions only narrow parent scope.

Construct OperationIntent internally. Execution/epoch/generation/spec/approval come from trusted execution state; principal/role/ancestry from authenticated registry; task/revision/owned scopes from admitted assignments. Parse patch targets and retain descriptor confinement at actual use. Classify structured argv using closed command grammar and actual isolation profile. Caller effects/target never authorize execution. Unknown commands are write-capable. A command's resource set is its entire allowed scope, not guessed actual writes. Narrow the OS profile or deny if a policy requires narrower access. Network/database/production identities require trusted configured profiles. Unresolvable or unenforceable required restrictions deny.

Persist opaque resource references only. Exact sensitive approvals reference immutable protected resources; never persist raw commands, secrets, or hashes derived from secret payloads. Include explicit classifications for outbox writes, environment preparation, delivery and AL commands; generic read labels cannot authorize their effects.

Closed failure codes include POLICY_INVALID, RESOURCE_UNRESOLVED, AUTHORITY_STALE, POLICY_DENIED, APPROVAL_REQUIRED, UNSUPPORTED_ACCOUNTING_DOMAIN, UNSUPPORTED_ENFORCEMENT_CAPABILITY. Missing optional policies preserves legacy authority; missing required authorization denies.

## Single authoritative ledger

Store approved contracts, pools, membership, attempts, capability uses and bounded decision audit in the execution's existing project state.db using existing transaction helper. execution_tree owner is stable root execution; workflow_instance is explicitly registered stable identity; named shared pools span only the same database. Reject cross-database or implicit machine-wide accounting. Global policy scope does not imply machine-wide counters.

Enforce foreign keys; unique(intent_id,pool_id), unique(capability_id,intent_id), stable attempt identity. Check arithmetic before addition and reject overflow. Pool identity does not depend on policy version, child, retry, native invocation or epoch. Immutable policy versions are separate from mutable active bindings. Replacement changes approved limits/version without resetting consumed/reserved. A limit below commitments denies new work; it does not erase charges. Admission and bounded audit commit together. Later observation projection deduplicates by decision ID. Public queries separate consumed/reserved/measured/unknown; NULL observed_units is never zero.

## Atomic admission and start

Lock order: OperationController.lock, shared epoch_action_lock, SQLite BEGIN IMMEDIATE. Never acquire controller/process locks inside the ledger transaction. Never hold SQLite/epoch locks across external work.

admit(intent):

1. Recheck exact current epoch/generation, approved active policy, principal revocation/release, spec and assignment inside the short transaction.
2. Existing intent returns its recorded state without a second execution permission.
3. Validate all exact capability bindings, expiry/use/unit limits, and every pool before changing any. available = limit - consumed - reserved. Required units must be <= available. One write-capable invocation requests one mutation unit; an identified retry requests one retry unit.
4. Insert durable reserved attempt, all reservations, capability-use records and allow decision atomically. Each pool uses guarded UPDATE reserved=reserved+units, revision=revision+1 WHERE active AND units<=limit-consumed-reserved; exactly one row required per pool or rollback all.
5. Increase capability uses in that transaction. Default maximum uses one. A nonstarted/failed attempt does not restore a capability use.
6. Commit is admission linearization point.

start_attempt(intent):

1. Immediately before first effect, reacquire shared epoch claim and transaction.
2. Revalidate current authority, policy digest, capability expiry/revocation and reserved state.
3. CAS reserved -> started; set trusted start/deadline; move discrete mutation/retry units from reserved to consumed. Commit.
4. This is the conservative irreversible start boundary. Only CAS winner receives a nonserializable, one-use in-memory permit. Release database/epoch claims before effects.

Revocation before admission denies. Revocation/replacement between reservation and start prevents start. Started attempts retain original accounting contract. Poststart failure, cancellation or no files changed never refunds discrete units. A committed start before actual execution may charge a crash window; this prevents unsafe duplicate effects. Denials persist closed bounded audit, never raw requests.

State machine: reserved -> started -> settled|unresolved; reserved -> released_proven_not_started. Denied start releases reserved capacity only through guarded release. release_unstarted must atomically cancel startability and fence the owner/launcher; start and release CAS cannot both win.

## Approval capabilities

Use existing Registry/developer decision workflow. Mint only from exact structured immutable authorization resource approved by that decision. Validate provenance, materialized delivery, current acknowledgment, exact execution/spec binding. Positive prose is insufficient.

Bind operation, canonical resources, principal/role, epoch/generation, policy digest, specification, use/unit maxima, expiration and revocation generation. Mint idempotently under shared claim and transaction. Admission compares every binding and enforces maximum uses and cumulative units. Revocation uses same ledger discipline, increments generation and never resets uses. Trusted supervisor time governs expiry; detected rollback/anomaly denies time-dependent grants until trusted time returns. Native callers reference capability IDs but cannot mint grants or submit preauthorized intents.

## Actual effect boundaries

Extend ProcessManager.start with internal attempt, absolute deadline, start and settlement hooks while preserving current public compatibility, owner/task/revision checks and containment. Preallocate handle and durable attempt before operation_popen; persist process linkage after launch. Call ledger callbacks outside ProcessManager.lock.

Prepare/validate parameters, isolation, process capacity and patch context before start where possible. Patch start hook runs before first parent_descriptor(create=True), because directory creation mutates. Preserve every descriptor/path/version check. Process start hook precedes operation_popen. Synchronous al_command uses same protocol as background operations. Absolute monotonic deadline passes into _drain; no fresh timeout at thread startup. Completion binds exact attempt/epoch/generation with supervisor provenance. Poststart launch errors retain charges; unknown launch outcome remains unresolved. Multifile partial patches count one invocation and retain truthful outcome.

Cover run_command, synchronous/background al_command, environment preparation, configured delivery/diagram handlers and AL managed-check launch paths before claiming enforcement. Read-only subprocesses still require tool/resource policies and deadlines.

Owned polling/cancel/result collection/essential reconciliation are separate closed control-plane classes: authenticate ownership and existing disclosure/classification checks; no mutation/retry charge or remaining-work-time requirement. Budget exhaustion must not make owned work impossible to stop/collect. Missing PID/handle or signal delivery is never closure proof.

## Retirement, recovery, settlement

settle obtains shared claim and transaction, validates explicit epoch/generation and CASes exact attempt. Started attempts never replay automatically, even without PID. Discrete units stay consumed; unknown variable usage retains full conservative bound. Qualified final provider accounting may settle only when enforced bound/final liability are proved.

Extend runtime _settled to include policy attempts. Before retirement prove owned execution closure or refuse activation. Persist unresolved accounting before transition; successor inherits commitments. Stale callbacks cannot write predecessor history or successor accounting. Current authority may reconcile against immutable predecessor evidence as a new authenticated action. Existing record_closed_context parent-exit checks alone do not prove no effect/no cost. No database/epoch lock while waiting for processes.

Crash windows: preadmission commit leaves nothing; postadmission leaves reservations; poststart leaves conservative consumption; posteffect/presettlement leaves unresolved outcome. Heartbeat or PID disappearance never refunds or enables replay. Uncertain process/launcher closure prevents unsafe activation/refund.

## Inheritance, retry, deadlines and usage

Child enrollment inherits parent/root policies and stable pools; fresh invocations/replacements cannot reset owners. Existing AL retry kind/reason/checkpoint/reconciliation/once-per-role guards remain authoritative in both shipped copies. Add budget intent tied to exact failed attempt/checkpoint. Crash after budget start before AL state save remains charged and requires explicit reconciliation; no autoplay.

Execution elapsed allowance is one shared wall interval including downtime, not summed child time. Persist origin/deadline, use monotonic clock while supervisor survives. Restart requires trustworthy clock continuity; unresolvable rollback/reboot denies new time-budgeted work while preserving cleanup. Effective operation timeout is min(requested,policy cap,all remaining execution allowances), preserving subsecond precision without upward rounding.

Explicit token/cost enforcement requires qualified conservative preadmission bounds, provider-enforced limit when necessary, adequate terminal accounting. Otherwise refuse before provider execution. Unspecified legacy caps preserve unavailable usage. Bytes are not tokens, unknown is not zero, provider exit/timeout does not prove no charge. Preserve observer/authentication restrictions.

Native child enrollment constrains Harness tool authority, not native generation. Codex pre-spawn policy hook is unavailable in inspected interfaces; requirements to prevent native delegation or enforce child provider limits must refuse unsupported enforcement before native execution. Do not substitute later enrollment.

Immutable runtime compatibility: enforcement activates only for verified execution profiles containing required hooks. Global configuration does not establish old immutable bridge enforcement. Reject unsupported configured activation/admission requirements before execution. Upgrade old runs only through supported transition; never hand-edit pins/bridge contents/authority. Report unsupported legacy coverage. Cleanup remains authenticated and usable after budget exhaustion.

## Required tests and support ordering

Admission races: last unit, overlapping pools, all-or-nothing capability/pool commit, integer bounds, duplicate intents. Capabilities: exact binding, one-use race, expiry/revocation ordering, approved-resource provenance, no capability refund. Recovery: each crash boundary, start/release race, retired settlement rejection, no PID refund, successor inheritance, clock uncertainty. Operations: forged effects/resources/role/epoch, sync/background paths, cleanup after exhaustion, uncapped compatibility. Files: hook before mkdir, confinement, partial patch. Epoch activation/admission/start/settlement races. Native unsupported pre-spawn enforcement refusal. Both AL retry copies preserve guards plus durable budget identity. Dashboard real measured/reserved/consumed/unknown and desktop/narrow visual checks.

EARLY_SUPPORT_SAFE: none. Builder follows stable query contracts, closed reason codes and exact supported configuration. Browser verification follows real admission -> settlement -> dashboard. Select next migration after prior increments stabilize. OPEN_QUESTIONS: [].
